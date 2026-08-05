import math
import time
from pathlib import Path

import cv2
import serial
from serial import SerialException
from ultralytics import YOLO


# ================= 1. 全局配置参数 =================
BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "model" / "best.pt"

COM_PORT = "COM6"          # 应打开设备管理器查看端口通道是com几
BAUD_RATE = 115200          # 必须与 STM32 一致
CAM_ID = 0                  # 首先可以尝试0，若不是之后可以尝试1，2。这取决于你把那个摄像头设为优先，默认是电脑自带摄像头（笔记本）
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
TARGET_CLASS = [0]          # 追踪指定类别；追踪所有类别时设为 None
CONF_THRESH = 0.5
INFERENCE_SIZE = 640        # YOLO 推理尺寸

SEND_HZ = 30                # 串口发送频率，建议 10~30 Hz
LOST_TARGET_VALUE = 9999    # 丢失目标时发送 [9999,9999]\n
PRINT_TX_PACKET = False     # True 时在终端打印每个发送包
CAMERA_FAIL_LIMIT = 10      # 连续读取失败多少次后退出
# ===================================================


def open_serial():
    """打开串口。失败时返回 None，程序仍可进入纯视觉演示模式。"""
    try:
        port = serial.Serial(
            port=COM_PORT,
            baudrate=BAUD_RATE,
            timeout=0.01,
            write_timeout=0.05,
        )
        print(f"✅ 串口 {COM_PORT} 打开成功，波特率 {BAUD_RATE}")
        return port
    except (SerialException, OSError) as exc:
        print(f"⚠️ 串口 {COM_PORT} 打开失败，进入纯视觉模式：{exc}")
        return None


def send_to_mcu(port, error_x: int, error_y: int) -> bool:
    """
    发送最简单的 ASCII 数据包：
        [x,y]\n
    例如：
        [35,-18]\n
    返回 True 表示写入成功；False 表示串口不可用或写入失败。
    """
    if port is None or not port.is_open:
        return False

    packet = f"[{int(error_x)},{int(error_y)}]\n"

    try:
        port.write(packet.encode("ascii"))
        if PRINT_TX_PACKET:
            print("TX:", packet.rstrip())
        return True
    except (SerialException, OSError) as exc:
        print(f"⚠️ 串口发送失败，后续切换为纯视觉模式：{exc}")
        try:
            port.close()
        except Exception:
            pass
        return False


def validate_target_classes(model: YOLO) -> None:
    """检查 TARGET_CLASS 是否超出当前模型的类别范围。"""
    if TARGET_CLASS is None:
        print("🎯 当前追踪：模型识别到的所有类别")
        return

    names = model.names
    valid_ids = set(names.keys()) if isinstance(names, dict) else set(range(len(names)))
    invalid_ids = [class_id for class_id in TARGET_CLASS if class_id not in valid_ids]

    if invalid_ids:
        raise ValueError(
            f"TARGET_CLASS 中存在无效类别编号 {invalid_ids}。当前模型类别表：{names}"
        )

    selected_names = [names[class_id] for class_id in TARGET_CLASS]
    print(f"🎯 当前追踪类别：{list(zip(TARGET_CLASS, selected_names))}")
    print(f"📚 当前模型完整类别表：{names}")


def main() -> int:
    serial_port = None
    cap = None

    try:
        if SEND_HZ <= 0:
            raise ValueError("SEND_HZ 必须大于 0")

        serial_port = open_serial()

        print("⏳ 正在加载 YOLO 模型……")
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"找不到模型文件：{MODEL_PATH}")

        model = YOLO(str(MODEL_PATH))
        validate_target_classes(model)

        print("⏳ 正在打开摄像头……")
        cap = cv2.VideoCapture(CAM_ID)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            print(f"❌ 无法打开编号为 {CAM_ID} 的摄像头")
            return 1

        print("🚀 视觉追踪已就绪；在视频窗口按 q 退出")
        print(
            f"📡 通信格式：[x,y]\\n；丢失目标发送 "
            f"[{LOST_TARGET_VALUE},{LOST_TARGET_VALUE}]\\n"
        )

        send_interval = 1.0 / SEND_HZ
        last_send_time = 0.0
        last_target_found = None

        fps_start_time = time.perf_counter()
        fps_count = 0
        current_fps = 0.0
        camera_fail_count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                camera_fail_count += 1
                print(
                    f"⚠️ 摄像头读取失败 {camera_fail_count}/{CAMERA_FAIL_LIMIT}",
                    end="\r",
                )
                if camera_fail_count >= CAMERA_FAIL_LIMIT:
                    print("\n❌ 摄像头连续读取失败，程序退出")
                    break
                time.sleep(0.02)
                continue

            camera_fail_count = 0

            height, width = frame.shape[:2]
            center_x = width // 2
            center_y = height // 2

            results = model.predict(
                frame,
                conf=CONF_THRESH,
                imgsz=INFERENCE_SIZE,
                verbose=False,
                classes=TARGET_CLASS,
            )

            result = results[0] if results else None
            target_found = False
            best_error_x = 0
            best_error_y = 0
            best_cx = 0
            best_cy = 0
            min_distance = float("inf")

            # 选择距离画面中心最近的目标。
            # 多目标非常接近时仍可能发生切换；需要更强锁定时应使用目标跟踪 ID。
            if result is not None and result.boxes is not None and len(result.boxes) > 0:
                for box in result.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    cx = int((x1 + x2) / 2)
                    cy = int((y1 + y2) / 2)
                    distance = math.hypot(cx - center_x, cy - center_y)

                    if distance < min_distance:
                        min_distance = distance
                        best_error_x = cx - center_x
                        # 保留原代码方向：目标在画面上方时，Y 为正。
                        best_error_y = center_y - cy
                        best_cx = cx
                        best_cy = cy
                        target_found = True

            # 限制发送频率；目标“出现/消失”时立即发送一次状态变化。
            now = time.perf_counter()
            should_send = (
                now - last_send_time >= send_interval
                or target_found != last_target_found
            )

            if should_send:
                if target_found:
                    tx_x, tx_y = best_error_x, best_error_y
                else:
                    tx_x = LOST_TARGET_VALUE
                    tx_y = LOST_TARGET_VALUE

                if serial_port is not None:
                    if not send_to_mcu(serial_port, tx_x, tx_y):
                        serial_port = None

                last_send_time = now
                last_target_found = target_found

            annotated_frame = result.plot() if result is not None else frame.copy()

            cv2.drawMarker(
                annotated_frame,
                (center_x, center_y),
                (0, 255, 0),
                cv2.MARKER_CROSS,
                30,
                2,
            )

            if target_found:
                cv2.circle(annotated_frame, (best_cx, best_cy), 8, (0, 0, 255), -1)
                cv2.line(
                    annotated_frame,
                    (center_x, center_y),
                    (best_cx, best_cy),
                    (0, 0, 255),
                    2,
                )

            fps_count += 1
            elapsed = now - fps_start_time
            if elapsed >= 1.0:
                current_fps = fps_count / elapsed
                fps_count = 0
                fps_start_time = now

            cv2.putText(
                annotated_frame,
                f"FPS: {current_fps:.1f}",
                (15, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 255),
                2,
            )

            status_text = "TARGET" if target_found else "LOST"
            cv2.putText(
                annotated_frame,
                status_text,
                (15, 78),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0) if target_found else (0, 0, 255),
                2,
            )

            cv2.imshow("Smart PTZ Tracker", annotated_frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        return 0

    except KeyboardInterrupt:
        print("\n🛑 用户中断程序")
        return 0
    except Exception as exc:
        print(f"❌ 程序运行失败：{exc}")
        return 1
    finally:
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass

        if cap is not None:
            cap.release()

        cv2.destroyAllWindows()
        print("✅ 摄像头、串口和窗口资源已释放")


if __name__ == "__main__":
    raise SystemExit(main())
