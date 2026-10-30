# 强制关闭

一个 Windows 小浮窗。拖动红色准星对准窗口，左键发送正常关闭请求；右键可查看对应进程、强制结束进程、关闭常规自启动项，并切换详情卡片。

## 下载最新版

[直接下载最新版 ForceClose.exe](https://github.com/jinmu666666-cmd/force-close/releases/latest/download/ForceClose.exe)，或查看 [最新版本发布页](https://github.com/jinmu666666-cmd/force-close/releases/latest)。双击运行，无需安装 Python。首次运行会请求管理员权限；如不授权，仍可使用普通权限，但部分窗口和进程无法操作。

## 使用

- 拖动准星周围的透明区域定位。可拖动区域为 64 × 64 像素，红色细准星位于中心。
- 左键：向准星下方窗口发送关闭请求。
- 右键：查看窗口与进程信息、强制结束进程、管理匹配的常规自启动项、开关详情卡片或退出。
- 关闭自启动前，程序会备份启动项；可在右键菜单恢复。备份存放于 `%LOCALAPPDATA%\窗口速关`，兼容旧版本备份。

强制结束进程会丢失该进程未保存的内容。程序无法管理服务、计划任务等自启动方式。透明拖动区域会接收其覆盖位置的鼠标操作。

## 从源码运行

需要 Windows、Python 3.12、`psutil` 和 `pywin32`：

```powershell
python -m pip install -r requirements.txt
python 强制关闭.py
```

程序默认请求管理员权限。开发或测试时可用 `python 强制关闭.py --no-elevate` 跳过权限请求。

## 构建独立程序

```powershell
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --onefile --windowed --name ForceClose --icon 强制关闭-准星.ico --add-data "强制关闭-准星.ico;." 强制关闭.py
```

产物位于 `dist\ForceClose.exe`。

## 许可证

MIT，见 [LICENSE](LICENSE)。
