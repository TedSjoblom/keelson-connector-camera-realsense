import logging
import zenoh
import warnings
import atexit
import json
import numpy as np
import time
from collections import deque
from threading import Thread, Event

import pyrealsense2 as rs
import cv2

import terminal_inputs
import keelson
from keelson.payloads.Image_pb2 import RawImage

def main():

    args = terminal_inputs.terminal_inputs()
    
    # Setup logger      
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=args.log_level
    )
    logging.captureWarnings(True)
    warnings.filterwarnings("once")

    ## Construct session
    logging.info("Opening Zenoh session...")
    conf = zenoh.Config()

    if args.connect is not None:
        conf.insert_json5(zenoh.config.CONNECT_KEY, json.dumps(args.connect))
    session = zenoh.open(conf)

    def _on_exit():
        session.close()

    atexit.register(_on_exit)


    # PUBLSIHERS
    key_point_cloud = keelson.construct_pubsub_key(
        realm=args.realm,
        entity_id=args.entity_id,
        subject="point_cloud",
        source_id=args.source_id,
    )
    publisher_point_cloud = session.declare_publisher(
        key_point_cloud,
        priority=zenoh.Priority.INTERACTIVE_HIGH(),
        congestion_control=zenoh.CongestionControl.DROP(),
    )
    logging.info(f"Publisher for point cloud at {key_point_cloud}")


    key_image_color = keelson.construct_pubsub_key(
        realm=args.realm,
        entity_id=args.entity_id,
        subject="raw_image",
        source_id=args.source_id + "/color",
    )
    publisher_image_color = session.declare_publisher(
        key_image_color,
        priority=zenoh.Priority.INTERACTIVE_HIGH(),
        congestion_control=zenoh.CongestionControl.DROP(),
    )
    logging.info(f"Publisher for image at {key_image_color}")

    key_image_depth = keelson.construct_pubsub_key(
        realm=args.realm,
        entity_id=args.entity_id,
        subject="raw_image",
        source_id=args.source_id + "/depth",
    )
    publisher_image_depth = session.declare_publisher(
        key_image_depth,
        priority=zenoh.Priority.INTERACTIVE_HIGH(),
        congestion_control=zenoh.CongestionControl.DROP(),
    )
    logging.info(f"Publisher for image at {key_image_color}")



    buffer = deque(maxlen=1)
    close_down = Event()

    def capture_frames():

        pipeline = rs.pipeline()
        config = rs.config()

        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30) # WORKS
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30) # WORKS

        # Start streaming
        pipeline.start(config)


        while True:
            # Wait for a coherent pair of frames: depth and color
            frames = pipeline.wait_for_frames()
            ingress_timestamp = time.time_ns()
            logging.info("Got new frame, at time: %d", ingress_timestamp)

            depth_frame = frames.get_depth_frame()
            color_frame = frames.get_color_frame()
            
            if not depth_frame or not color_frame:
                continue

            # Convert images to numpy arrays
            depth_image = np.asanyarray(depth_frame.get_data())
            logging.debug("Depth image shape: %s", depth_image)

            color_image = np.asanyarray(color_frame.get_data())

            # Apply colormap on depth image (image must be converted to 8-bit per pixel first)
            depth_colormap = cv2.applyColorMap(
                cv2.convertScaleAbs(depth_image, alpha=0.03), cv2.COLORMAP_JET
            )

            buffer.append((depth_colormap, color_image, ingress_timestamp))
    
    # Start capture thread
    t = Thread(target=capture_frames)
    t.daemon = True
    t.start()


    try:
        previous = time.time()

        while True:
            try:
                depth_colormap, color_image, ingress_timestamp = buffer.pop()
            except IndexError:
                time.sleep(0.01)
                continue

            logging.debug("Processing raw frame")

            height_dep, width_dep, _ = depth_colormap.shape
            data_dep = depth_colormap.tobytes()
            width_step_dep = len(data_dep) // height_dep 

            height_col, width_col, _ = color_image.shape
            data_color = color_image.tobytes()
            width_step_color = len(data_color) // height_col

            logging.debug(
                "Frame total byte length: %d, widthstep: %d", len(data_color), width_step_color
            )

            if "raw_color" in args.publish:
                logging.debug("Send RAW COLOR frame...")
                payload = RawImage()
                payload.timestamp.FromNanoseconds(ingress_timestamp)
                if args.frame_id is not None:
                    payload.frame_id = args.frame_id
                payload.width = width_col
                payload.height = height_col
                payload.encoding = "bgr8"  # Default in OpenCV
                payload.step = width_step_color
                payload.data = data_color

                serialized_payload = payload.SerializeToString()
                envelope = keelson.enclose(serialized_payload)
                publisher_image_color.put(envelope)
                logging.debug(f"...published on {key_image_color}")

            if "raw_depth" in args.publish:
                logging.debug("Send RAW DEPTH frame...")
                payload = RawImage()
                payload.timestamp.FromNanoseconds(ingress_timestamp)
                if args.frame_id is not None:
                    payload.frame_id = args.frame_id
                payload.width = width_dep
                payload.height = height_dep
                payload.encoding = "bgr8"
                payload.step = width_step_dep
                payload.data = data_dep

                serialized_payload = payload.SerializeToString()
                envelope = keelson.enclose(serialized_payload)
                publisher_image_depth.put(envelope)
                logging.debug(f"...published on {key_image_depth}")



    except KeyboardInterrupt:
        logging.info("Closing down on user request!")

        logging.debug("Joininye :)")




if __name__ == "__main__":
    main()