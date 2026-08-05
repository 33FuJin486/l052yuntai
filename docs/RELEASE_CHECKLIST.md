# Windows 发布与验收清单

## 构建前

- [ ] Windows 10/11 64 位构建机安装 Python 3.10.x。
- [ ] `model/best.pt` 可由源码运行环境成功加载。
- [ ] 执行 `python -m unittest discover -s tests -v` 全部通过。
- [ ] 在真实摄像头和 STM32 上运行 `python main.py`。
- [ ] 对照原程序确认 X/Y 符号与最近目标选择。

## 构建

- [ ] 双击 `build.bat`，构建无错误。
- [ ] 生成 `dist/云台视觉跟踪系统/云台视觉跟踪系统.exe`。
- [ ] 发布目录存在 `model/best.pt` 与 `config/settings.json`。
- [ ] 启动时没有黑色控制台窗口。

## 无 Python 电脑验收

- [ ] 测试机未安装 Python、Anaconda、PySide6、Ultralytics、OpenCV。
- [ ] 复制完整发布文件夹后可双击启动。
- [ ] 模型加载成功，类别显示 `logo`。
- [ ] 可扫描、打开和关闭摄像头。
- [ ] BGR/RGB 颜色正确。
- [ ] 检测框、中心点、连线、误差与 FPS 正确。
- [ ] 可扫描、连接和断开 STM32 串口。
- [ ] CH340/CH341 串口在列表中正确标注。
- [ ] 无串口时出现检查提示，“驱动帮助”只在用户确认后打开沁恒官网，不自动安装。
- [ ] “启动识别”不自动发送数据。
- [ ] “开始发送”后 STM32 正常跟踪。
- [ ] 目标丢失后串口发送 `[9999,9999]` 并停车。
- [ ] “暂停发送”和“紧急停止”立即停车。
- [ ] 拔出 STM32 后软件不崩溃，可重新连接。
- [ ] 拔出摄像头后软件不崩溃，可重新打开。
- [ ] 关闭窗口后摄像头指示灯熄灭、串口释放、无残留进程。
- [ ] 替换 `model/best.pt` 后无需重新打包。

## 发布记录

在通过验收的 Windows 构建环境执行：

```bat
python --version
python -m pip freeze > verified-requirements.txt
```

保存 Windows 版本、CPU/CUDA 类型、依赖锁定文件、测试机型号、摄像头和 USB 转串口芯片信息。
