# 云台视觉跟踪系统

Windows 桌面版 YOLO 云台视觉跟踪软件。软件在独立视觉线程中读取摄像头并执行 YOLO 推理，通过原有 ASCII 协议向 STM32 发送中心误差。

## 关键安全语义

- 正常帧：`[error_x,error_y]\n`
- 目标丢失/安全停车帧：`[9999,9999]\n`
- `error_x` 右侧为正；`error_y` 上方为正。
- 默认发送频率 30 Hz；目标出现/消失时立即发送状态变化。
- “暂停发送”“停止识别”“紧急停止”和程序退出都会阻止继续发送旧误差；连接正常时会发送安全停车帧。
- STM32 端收到安全停车帧后把两轴速度清零并复位 PID；超过 500 ms 未收到有效数据也会自动停车。

详细协议见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

## 目录

```text
YOLO_VISION/
├─ main.py
├─ app/
│  ├─ main_window.py
│  ├─ vision_worker.py
│  ├─ vision_core.py
│  ├─ serial_manager.py
│  ├─ protocol.py
│  ├─ driver_support.py
│  ├─ config_manager.py
│  └─ resource_path.py
├─ model/best.pt
├─ config/settings.json
├─ tests/
├─ docs/
├─ requirements.txt
├─ 云台视觉跟踪系统.spec
└─ build.bat
```

## 开发环境运行

要求 Windows 10/11 与 Python 3.10.x。

```bat
py -3.10 -m venv .venv
call .venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

模型使用 Ultralytics 8.4.103 训练，类别表为 `{0: "logo"}`。第一次启动会自动加载 `model/best.pt`，但不会自动打开摄像头或串口。

## 使用步骤

1. 双击 `云台视觉跟踪系统.exe`，等待右上角显示模型已加载。
2. 在“摄像头控制”中点击“刷新摄像头”，选择设备与分辨率，再点击“打开摄像头”。
3. 在“串口控制”中点击“刷新串口”，选择 STM32 对应的 COM 口，默认波特率 115200，点击“连接串口”。CH340/CH341 设备会在列表中标注。
4. 点击“启动识别”。此时只显示识别画面，不会发送控制数据。
5. 确认检测框、中心点和误差方向正确后，点击“开始发送”。
6. 需要暂时停机时点击“暂停发送”；需要停止推理时点击“停止识别”。
7. 异常情况下点击红色“紧急停止”。软件会立即停止正常误差发送并发送 `[9999,9999]`，摄像头保持打开。
8. 关闭窗口时软件自动停车、关闭串口、释放摄像头并等待视觉线程退出。

## 选择或更换模型

- 默认模型位于 `model/best.pt`。
- 点击“选择…”可使用其他 `.pt` 文件，再点击“加载模型”。
- 运行中切换模型前必须先停止识别。
- 发布后可直接替换发布目录中的 `model/best.pt`，无需重新打包。
- 新模型类别表不同后，需要同步修改“检测类别”。追踪全部类别输入 `None`。

## 配置保存

关闭软件时配置自动写入外部 `config/settings.json`。如果保存的 COM 口或摄像头在新电脑不存在，界面会使用当前扫描结果，不会自动连接不存在的设备。配置文件损坏时会回退默认值并写入日志。

参数说明见 [docs/PARAMETERS.md](docs/PARAMETERS.md)。

## Windows 打包

在 Windows 10/11、Python 3.10.x 环境双击：

```text
build.bat
```

脚本会创建 `.venv`、安装依赖、执行 PyInstaller `--onedir --windowed` 等价构建，并把外部模型与配置复制到：

`build.bat` 会先从 PyTorch 官方 CPU wheel 仓库安装 `torch/torchvision`，因此该脚本产出的是第一版 CPU 兼容包。

```text
dist/云台视觉跟踪系统/
├─ 云台视觉跟踪系统.exe
├─ model/best.pt
├─ config/settings.json
├─ README.md
└─ DLL 与 Python 运行依赖
```

发布时必须复制整个文件夹，不能只复制 EXE。第一版建议在 CPU 构建机创建 CPU 兼容包；CUDA 版应使用独立环境、独立名称和对应 NVIDIA 驱动验收。

> 当前源码包包含可重复构建脚本，但 Linux 工作环境不能生成或验证 Windows EXE。最终发布目录必须在 Windows 上构建，并在没有 Python/Anaconda 的另一台 Windows 电脑完成实机验收。

## 自动化测试

不连接摄像头和串口即可运行协议、配置和目标筛选回归测试：

```bat
call .venv\Scripts\activate
python -m unittest discover -s tests -v
```

硬件验收步骤见 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)。

## 常见问题

### 串口连接失败

- 点击“刷新串口”，不要沿用另一台电脑的 COM 编号。
- 关闭串口助手、IDE 串口监视器等占用程序。
- 确认 USB 转串口驱动和波特率 115200。
- 若列表显示“未发现串口”，先检查设备是否通电、USB 线是否支持数据传输；若使用 CH340/CH341，点击软件中的“CH340/CH341 驱动帮助”，或访问[沁恒官方 CH341SER 驱动页](https://www.wch.cn/downloads/ch341ser_exe.html)。
- 软件不会静默下载或强制安装驱动；是否下载、安装由用户在官方页面自行决定。
- 拔出 STM32 后软件会停止发送并显示断开，可重新刷新和连接。

### 摄像头打开失败

- 点击“刷新摄像头”后重新选择编号。
- 关闭会议软件、浏览器和其他占用摄像头的程序。
- 尝试 640×480；确认摄像头驱动已安装。
- 中途拔出后，软件会在连续读取失败达到阈值时停止识别并释放设备。

### 模型加载失败

- 确认发布目录中存在 `model/best.pt`。
- 路径可以是相对程序目录的路径，也可以通过界面选择绝对路径。
- 新模型若没有类别 0，需要调整“检测类别”或输入 `None`。

更多排查方法见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。
