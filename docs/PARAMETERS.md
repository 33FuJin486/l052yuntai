# 参数说明

| 配置项 | 默认值 | 含义 |
|---|---:|---|
| `serial_port` | 空 | 上次选择的串口；启动时不自动连接 |
| `baudrate` | 115200 | 必须与 STM32 USART1 一致 |
| `camera_index` | 0 | OpenCV 摄像头编号 |
| `frame_width` | 640 | 期望采集宽度；实际值由驱动决定 |
| `frame_height` | 480 | 期望采集高度；实际值由驱动决定 |
| `model_path` | `model/best.pt` | 相对 EXE/项目根目录的外部模型路径 |
| `confidence` | 0.5 | YOLO 置信度阈值，范围 0.01–1.0 |
| `inference_size` | 640 | YOLO `imgsz` |
| `target_classes` | `[0]` | 目标类别列表；`null` 表示全部类别 |
| `send_frequency` | 30 | 串口发送 Hz，建议 10–30 |

## 当前模型

- 模型：YOLO11n detection。
- Ultralytics 保存版本：8.4.103。
- 类别：`0 = logo`。
- 模型文件约 5.3 MB。

## 目标筛选

YOLO 先按 `confidence` 和 `target_classes` 过滤。多目标情况下，软件选择检测框中心到画面中心欧氏距离最小的目标。检测框中心先转换为整数，再计算误差和距离；距离相同时保留 YOLO 结果中的第一个框。

## CPU 与 CUDA

软件启动时使用 `torch.cuda.is_available()` 自动检测：

- 可用时使用设备 `0`，界面显示 CUDA 显卡名称。
- 不可用时使用 CPU。

发布包应明确区分 CPU 版与 CUDA 版。第一版建议使用 CPU 依赖构建以提高换机兼容性。
