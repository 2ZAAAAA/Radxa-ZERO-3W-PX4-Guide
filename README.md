# Radxa ZERO 3W × Pixhawk 6C：无屏 Ubuntu 与 ROS 2 串口通信实录

这份教程分两条主线：先把 Radxa 配成仅接电源也能联网、可用 Windows 远程操作的 Ubuntu 小电脑；再让它通过 GPIO UART 和 Pixhawk 6C 的 TELEM2 建立 ROS 2 通信。[故障复盘与恢复命令](故障排查.md)记录本次真正卡住的地方，正常安装不需要照着排查篇逐条执行。

本文记录的是台架通信。接线前拆桨、断开两端所有电源（包括可能给飞控供电的 USB），测试过程中不解锁。光流、雷达、飞行前检查和链路负载需要分别验收。

- [主线一：Radxa 系统配置](#主线一radxa-系统配置)
- [主线二：Radxa 与 PX4 通信](#主线二radxa-与-px4-通信)
- [本次验收结果](#本次验收结果)
- [操作详解：读懂命令、正常输出与每步验收](操作详解.md)
- [故障复盘与恢复命令](故障排查.md)
- [实测过程与验收表](实测过程与验收.md)

## 实测环境

| 项目 | 本次版本 / 配置 |
| --- | --- |
| 上位机 | Radxa ZERO 3W，1 GiB RAM，microSD 启动 |
| 镜像 | 社区项目 Joshua-Riek/ubuntu-rockchip **v2.4.0**，`ubuntu-22.04-preinstalled-server-arm64-radxa-zero3.img.xz` |
| 启动后的系统 | Ubuntu **22.04.5 LTS Server** ARM64；内核 **5.10.0-1012-rockchip** |
| 桌面与远程 | XFCE，xrdp，TigerVNC 服务端；Windows 实测 TigerVNC Viewer 1.16.0 |
| 开发环境 | GCC 11.4.0、CMake 3.22 系列、Python 3.10 系列，以及 G++、make、Ninja、Git、GDB、pip/venv、pyserial、minicom；2 GiB swap |
| ROS 2 | Humble `ros-base`，colcon、rosdep、vcstool，系统安装的 Fast DDS / Fast CDR |
| PX4 通信 | `px4_msgs` 的 `release/1.17`；Micro XRCE-DDS Agent **v2.4.2** |
| 下位机 | Holybro Pixhawk 6C，已有 PX4 **1.17.0**；本流程没有重刷飞控固件 |
| UART | Radxa UART4_M1 `/dev/ttyS4` ↔ Pixhawk TELEM2 `/dev/ttyS3`；**115200**，3.3 V 逻辑 |

Ubuntu Rockchip 是面向具体板型的社区镜像，不是 Canonical 的通用 ARM64 镜像。本文固定记录实际能复现的 **5.10** 启动配置；旧 Radxa Debian 系统曾用 6.1，那是另一套镜像，不把两套的 Wi‑Fi 或设备树命令混用。桌面背景是 XFCE 默认壁纸，只反映桌面环境；用 `/etc/os-release` 确认 Ubuntu 版本。

本文区分三个命令执行位置：**Windows PowerShell**、**Radxa Ubuntu 终端**（SSH、本地或 VNC）和 **QGC → 分析工具 → MAVLink 控制台**（飞控）。代码块前会标明位置。以下 PowerShell 命令从本仓库根目录执行。

## 主线一：Radxa 系统配置

### A1. 烧录 microSD，第一次开机前写 Wi‑Fi

1. 先保留旧卡或完整备份。到 [Ubuntu Rockchip v2.4.0 发布页](https://github.com/Joshua-Riek/ubuntu-rockchip/releases/tag/v2.4.0) 的 Assets 选择上表完整文件名，用 Etcher 写到 Radxa 的 microSD。确认容量和目标设备；烧录会覆盖选中的卡。
2. 重新插读卡器，找到含 `user-data` 和 `network-config` 的启动分区（常见卷标 `CIDATA`）。Windows 弹出“需要格式化”时取消，它可能只是看不懂 Linux 分区。
3. **保持板子还没用这张卡启动过**。在 Windows PowerShell 执行下列命令，将 `F:\` 换成卡的实际启动盘符：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ubuntu22-setup\prepare-first-boot.ps1 -BootPath 'F:\'
```

在本地输入手机热点和实验室 Wi‑Fi 的准确名称及密码。脚本以**手机热点**作为首次启动网络，实验室凭据此时只是保存起来，后面才会加入自动连接。建议首次使用广播名称、2.4 GHz、WPA2 密码热点；脚本面向普通 WPA2-PSK，不适用于 802.1X 或需要网页登录的校园网。脚本写入的启动文件和备份可能含有效凭据，Base64 也可以还原，请留在本地。

完成后安全弹出卡，插回板子。开启热点，优先使用稳定的 5 V / 2 A 或以上能力的 USB-C 电源，等首次初始化。若手机数据慢，可先用热点连上板子，再参考[实验室网络恢复](故障排查.md#lab-wifi)切到有正常 Internet 出口的 Wi‑Fi 安装。

### A2. 查 IP，登录 SSH

在手机热点设备列表或实验室路由器中查板子当前 IP；有屏幕时可在 **Radxa** 直接查。Windows 要在能够到达该 IP 的网络里；仅仅能访问互联网，并不代表能访问板子的局域网地址。

**Radxa Ubuntu 终端**：

```bash
ip -br addr
hostname -I
cat /etc/os-release
uname -rm
```

**Windows PowerShell**，用实际 IP 替换示例地址 `192.0.2.10`：

```powershell
$BoardIP = '192.0.2.10'
Test-NetConnection -ComputerName $BoardIP -Port 22
ssh "ubuntu@$BoardIP"
```

该首次启动模板的临时登录是 `ubuntu` / `ubuntu`，首次登录时改密码。重刷后若相同 IP 对应的新主机密钥与旧记录冲突，先在板子的本地终端运行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`，核对目标指纹，再在 Windows 执行 `ssh-keygen -R $BoardIP` 移除这个地址的旧记录。不要关闭全局主机校验。

### A3. 分阶段安装开发工具、桌面、ROS 2 与通信组件

**Windows PowerShell**：先准备 ROS 软件源配置包和依赖快照，再上传整个 `ubuntu22-setup`。该下载在能访问 GitHub 的电脑上执行；它不下载全部 APT 软件或 PX4 源码。若板子已切换到实验室网络，更新 `$BoardIP` 后再传。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ubuntu22-setup\prepare-ros-assets.ps1
scp -r .\ubuntu22-setup "ubuntu@${BoardIP}:~/"
ssh "ubuntu@$BoardIP"
```

**Radxa Ubuntu 终端**：下面按阶段执行，一个成功后再执行下一个。首次登入后若 cloud-init 还在跑，先等它结束。若报错，保留错误并从对应阶段继续，避免重复跑完整套安装。

```bash
sudo cloud-init status --wait
sudo python3 ~/ubuntu22-setup/setup.py base --keep-network
sudo python3 ~/ubuntu22-setup/setup.py ros
sudo python3 ~/ubuntu22-setup/setup.py px4 --px4-version 1.17
sudo python3 ~/ubuntu22-setup/setup.py check
```

| 阶段 | 安装 / 生成内容 | 完成标记 |
| --- | --- | --- |
| `base --keep-network` | 保留当前 Wi‑Fi；安装 SSH、XFCE、xrdp、TigerVNC 和开发工具；低内存时补 swap | `BASE READY` |
| `ros` | ROS 2 Humble、colcon、rosdep、vcstool；本地依赖快照生成 rosdep 缓存 | `ROS READY` |
| `px4 --px4-version 1.17` | 下载 `px4_msgs` 和 Agent，在 `~/ros2_px4_ws` 使用 colcon 单任务编译 | `2 packages finished`、`PX4 BUILD READY` |
| `check` | 检查服务、IP、内存、端口、ROS 环境 | `Checks complete`，并核对上方实际输出 |

`base` 会提示设置独立的 VNC/RDP 密码，设置 6–8 个字符，`view-only` 选 `n`。SSH 用 Ubuntu 用户密码。这里的 `--px4-version 1.17` 选择**上位机消息定义版本**；Agent 也安装在 Radxa 上。编译阶段不刷写 PX4、不动飞控 PID 或标定，也不会启用 GPIO UART。

同一次新装也可用 `sudo python3 ~/ubuntu22-setup/setup.py all --keep-network --px4-version 1.17` 替代上述 `base`、`ros`、`px4`、`check` 四行；做法二选一。1 GiB 板上，本次编了约 **2 小时 32 分钟**，不要把百分比久不动等同于出错。完成后核对：

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
printenv ROS_DISTRO
ros2 pkg prefix px4_msgs
command -v MicroXRCEAgent
free -h
swapon --show
gcc --version | head -n 1
cmake --version | head -n 1
python3 --version
git -C ~/ros2_px4_ws/src/px4_msgs rev-parse HEAD
git -C ~/ros2_px4_ws/src/Micro-XRCE-DDS-Agent describe --tags --exact-match
```

### A4. 开机优先热点，关闭热点回到实验室 Wi‑Fi

**Radxa Ubuntu 终端**：从已能 SSH、且 `base` 已完成的状态运行。新方案在重启时生效，建议初次切换时留着显示器或键盘作为恢复途径。看到 `NETWORK PREPARED` 再重启；这一步不要额外远程运行 `netplan apply`。

```bash
sudo python3 ~/ubuntu22-setup/setup.py network
# 看到 NETWORK PREPARED 后再执行：
sudo reboot
```

`network` 创建 `radxa-phone` 和 `radxa-lab` 两个 NetworkManager 配置，优先级分别为 `100` 和 `10`。优先级用于挑选自动连接；它不会把已经连着的实验室网络立即抢占掉。`radxa-hotspot-prefer.timer` 补上这个行为：当板子正在使用实验室配置时，约每 30 秒扫描一次，连续两次看到热点才尝试切换。切过去可能需要一到两分钟；失败时会尝试恢复实验室网络。关闭热点后由 NetworkManager 自动恢复到实验室 Wi‑Fi。

```bash
nmcli device status
nmcli -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show
ip -br addr
systemctl list-timers radxa-hotspot-prefer.timer
sudo journalctl -b -u radxa-hotspot-prefer.service --no-pager -n 30
```

先关闭热点确认实验室能连，再开热点验证切过去，最后关热点验证回退。每次切换都按**新 IP**重连，笔记本也要切到能访问板子的网络。在不希望主动切换的场合，可暂停定时器；这不会删除两套 Wi‑Fi 配置：

```bash
sudo systemctl disable --now radxa-hotspot-prefer.timer
# 需要恢复主动切换时：
sudo systemctl enable --now radxa-hotspot-prefer.timer
```

### A5. Windows 远程桌面和独立 VNC

两种客户端进入**同一个 XFCE 虚拟桌面**。TigerVNC 产生 `:1`（板子本机 TCP 5901），xrdp 可把 Windows RDP 转到它，拔掉 HDMI 后也能运行。

- **RDP**：Windows 运行 `mstsc`，电脑栏填板子当前 IP。在 xrdp 中选 **Radxa XFCE**，使用设置的 VNC 主密码。这个方案不用旧 Debian 的 `Xorg` 会话。
- **VNC**：在 Windows 用以下命令建立 SSH 隧道并保持窗口打开。输入 SSH 密码后窗口保持安静属于正常现象。用 **TigerVNC Viewer** 连接 `127.0.0.1::15901`（双冒号表示端口），填 VNC 主密码。RealVNC Viewer 和 TigerVNC Viewer 都是客户端；本文实测的是 TigerVNC Viewer，无需另外装 RealVNC Server。

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:15901:127.0.0.1:5901 "ubuntu@$BoardIP"
```

**Radxa Ubuntu 终端**：检查服务与端口。随后正常关机，拔 HDMI，只接电源重新开机，再用 SSH、RDP 或上述 VNC 步骤重连。VNC 的 5901 只留在本机，通过隧道访问，无需在路由器上开放端口。

```bash
systemctl is-active ssh xrdp radxa-desktop.service
ss -ltn | grep -E ':(22|3389|5901)[[:space:]]'
sudo poweroff
```

## 主线二：Radxa 与 PX4 通信

### B1. 备份飞控参数，确认接口分配

在 **QGC → 车辆设置 → 参数 → 工具 → 保存到文件**，保存修改前的 `.params`。数传连接 QGC 与 Radxa 的 DDS 串口是两条不同链路，QGC 能看到飞控不能单独证明 Radxa 已连通。

| Pixhawk 6C 端口 | 本例配置 | 是否改变 |
| --- | --- | --- |
| TELEM1 | 数传 / MAVLink；本机 `MAV_0_CONFIG=101` | 保留 |
| TELEM2 | 从光流改给 Radxa；`UXRCE_DDS_CFG=102` | 修改 |
| TELEM3 | TFmini PLUS；本机 `SENS_TFMINI_CFG=103` | 保留 |
| GPS2 | 本机原先空闲；本例将 ThoneFlow-3901UY UART 迁来，`SENS_TFLOW_CFG=202` | 有占口时才需要迁移 |

若你的 TELEM2 原本就在空闲，直接留给 DDS 即可。若它被数传、光流或其他驱动占用，先按你的实际设备安排替代端口；不要把同一个 UART 同时交给 MAVLink 和 DDS。本文没有改 PID、EKF 和已有标定；如改变了传感器安装位置或方向，另行核对其方向和输出。

### B2. 在 Radxa 启用 UART4_M1

以下只适用于本文镜像的 **U-Boot / extlinux** 布局。先只读确认文件和 overlay 存在：

```bash
uname -r
ls -l /etc/default/u-boot /boot/extlinux/extlinux.conf
find -L /lib/firmware/"$(uname -r)" -name 'rk3568-uart4-m1.dtbo'
grep -nE '^[[:space:]]*U_BOOT_FDT_OVERLAYS=' /etc/default/u-boot
```

本例确认此前没有 `U_BOOT_FDT_OVERLAYS`，所以可用下列命令。该变量若已有值，请把 UART4 overlay 加入**现有列表**，保留原项；不要重复写第二个赋值。如果文件或目录不符，先查所用镜像的 overlay 方法，不照搬。

```bash
sudo bash -e <<'EOF'
test -f /lib/firmware/"$(uname -r)"/device-tree/rockchip/overlay/rk3568-uart4-m1.dtbo
! grep -qE '^[[:space:]]*(export[[:space:]]+)?U_BOOT_FDT_OVERLAYS=' /etc/default/u-boot || exit 1
cp -an /etc/default/u-boot /etc/default/u-boot.before-uart4
cp -an /boot/extlinux/extlinux.conf /boot/extlinux/extlinux.conf.before-uart4
printf '\nU_BOOT_FDT_OVERLAYS="device-tree/rockchip/overlay/rk3568-uart4-m1.dtbo"\n' >> /etc/default/u-boot
u-boot-update
grep -iE '^[[:space:]]*fdtoverlays[[:space:]].*rk3568-uart4-m1\.dtbo' /boot/extlinux/extlinux.conf
echo 'UART4 CONFIG READY'
EOF
```

只有看到 `UART4 CONFIG READY` 才执行 `sudo reboot`。重启后执行下列命令，应见 `/dev/ttyS4`，设备树节点末尾是 `serial@fe680000`，用户在 `dialout` 组中：

```bash
ls -l /dev/ttyS4
readlink -f /sys/class/tty/ttyS4/device/of_node
id -nG
```

### B3. 飞控参数、迁移光流和接线

**QGC MAVLink 控制台**。下列 `SENS_TFLOW_CFG` 只用在本文这台原先由光流占 TELEM2、且 GPS2 确认空闲的飞控。其他人可跳过这一行。也可在 QGC 参数页搜索完整名字，例如搜索 `UXRCE_DDS_CFG` 选 `TELEM 2`，不要把端口选择和波特率当成同一个参数。

```text
param show SENS_TFLOW_CFG
param show GPS_2_CONFIG
param show UXRCE_DDS_CFG
param show SER_TEL2_BAUD
param set SENS_TFLOW_CFG 202
param set UXRCE_DDS_CFG 102
param save
```

PX4 的串口波特率参数可能在分配设备并重启后才可见。需要时先重启飞控并重新连接 QGC，再运行：

```text
param set SER_TEL2_BAUD 115200
param save
```

重启飞控并重新连接 QGC 后：

```text
param show SER_TEL2_BAUD
mavlink status
```

**断电后接线**，以厂家端口图和实际 Pin 1 标记确认方向。下表是**物理针号**，不是 GPIO 编号，线色也不能用来确定针脚。

| Pixhawk 6C TELEM2 | Radxa ZERO 3W 的 40 针排针 | 功能 |
| --- | --- | --- |
| Pin 2，飞控 TX | Pin 16，UART4 RX | 飞控发，Radxa 收 |
| Pin 3，飞控 RX | Pin 18，UART4 TX | Radxa 发，飞控收 |
| Pin 6，GND | Pin 6，GND | 共地 |

- **不接 TELEM2 的 Pin 1（+5 V）、Pin 4、Pin 5 到 Radxa。** 台架上 Radxa 通过自己的 USB-C 口取稳定 5 V；Pixhawk 用适配的飞控电源模块。若沿用 3S 电池供电，走原本适配的电源模块，不把电池原电压接到飞控端口或 Radxa。三根 UART 线只传数据与共地。
- 本例光流插头实际只用 **1（原模块供电）、3（飞控 RX）、6（GND）**。核对 Holybro GPS2 对应功能、连接器方向和原供电转接后，才将这条线束由 TELEM2 按原线序移到 GPS2。若原线束使用 4/5 或不是同样的转接方式，不能照搬；GPS2 的 4/5 是 I²C，本例不接。保留原模块侧供电转换，不把裸传感器直接接未知电压。
- 有条件用带锁扣的线束；原型杜邦线接触不稳时，先断电修线，再继续复验。见[串口故障复盘](故障排查.md#dds-disconnected)。

### B4. 先连通，再部署 Agent 开机自启

首次可以手动前台运行 Agent，便于看日志。**Radxa Ubuntu 终端**：仅在尚未运行 systemd Agent 时执行；已部署则用日志查看，不能让两个进程同时占 `/dev/ttyS4`。飞控设置已保存并重启后，由 PX4 自动启动 Client，不需要 QGC 手动启动它。

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
MicroXRCEAgent serial --dev /dev/ttyS4 -b 115200 -v 4
```

**QGC MAVLink 控制台**：

```text
uxrce_dds_client status
```

看到 `Running, connected`，另开 Radxa 终端验证 B5 的新消息。随后在前台 Agent 窗口按 `Ctrl+C`，在 **Radxa** 安装仓库随附的服务：

```bash
sudo install -m 0644 ~/ubuntu22-setup/radxa-px4-agent.service /etc/systemd/system/radxa-px4-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now radxa-px4-agent.service
systemctl is-enabled radxa-px4-agent.service
systemctl is-active radxa-px4-agent.service
journalctl -b -u radxa-px4-agent.service --no-pager -n 40
```

服务以 `ubuntu` 用户运行，并具备 `dialout` 权限。它自行加载 ROS 和工作空间，打开 `/dev/ttyS4` 并在退出后重试。若用户、目录或串口不同，先调整服务模板。两个环境配置的位置分别是：

```bash
# 每次打开交互式 Bash 时由 ~/.bashrc 加载；只检查即可。
grep -nE 'humble/setup\.bash|ros2_px4_ws/install/setup\.bash' ~/.bashrc
# 开机时由 systemd 加载；与登录终端、VNC 和 QGC 无关。
systemctl cat radxa-px4-agent.service
```

### B5. 分别验证链路、接收和双向数据

1. **QGC MAVLink 控制台**：`uxrce_dds_client status` 看是否 `Running, connected`，以及有没有完成时间同步。对于 Agent，`active` 只说明它正在运行。
2. **Radxa Ubuntu 终端**：实际订阅一条新的飞控状态，确认 PX4 → ROS 2。仅仅看到话题名字，不能证明收到了消息。

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_px4_ws/install/setup.bash
ros2 topic list -t
ros2 service list -t
timeout 30s ros2 topic echo /fmu/out/vehicle_status_v1 \
  --qos-reliability best_effort --field timestamp --once
```

3. **Radxa Ubuntu 终端**：在这台已拆桨、未解锁的台架上，请求 PX4 重新运行飞行前检查。以下 `target_system: 1` 对应本例的 `MAV_SYS_ID=1`；非默认 ID 应先在 QGC 核实。若有命名空间，先用 `ros2 service list -t` 查实际服务名。

```bash
timeout 15s ros2 service call /fmu/vehicle_command px4_msgs/srv/VehicleCommand \
  "{request: {timestamp: $(date +%s%6N), command: 401, target_system: 1, target_component: 1, source_system: 1, source_component: 1, from_external: true}}"
```

本例收到 `command=401, result=0`，确认请求到达飞控且回执到达 Radxa。它不表示飞行前检查通过，本文也没有验证电机、解锁或飞行控制。实际检查结果仍看 `VehicleStatus` 和 QGC。

### B6. 冷启动与传感器检查

在 Radxa 正常执行 `sudo poweroff`，完全断开两块板子的电源（留意飞控 USB 可能仍供电），然后恢复上电。固定一种顺序做第一次对照，本次用过**先飞控、后 Radxa**。这不是协议要求：Agent 可先在串口等待，Client 可先启动并探测；本文未在这组设备上单独记录反过来上电的结果。测试期间不用手动启动任何通信进程，也不用打开 QGC 才能握手。

重新 SSH 后，先检查 Agent 本次启动，再重复 B5。刚开机可把状态订阅等候时间放到 180 秒。若任一步超时，保持现状，记录本次 Agent 日志和飞控 Client 状态，再按[故障篇](故障排查.md#dds-disconnected)区分自启与串口连通。不要把一次 `active` 或一次成功当成长期稳定。

**QGC MAVLink 控制台**：更换过接口的传感器要独立检查，没移动过的则读取原状态对照。量程、更新和运动响应分开核验。

```text
thoneflow status
listener sensor_optical_flow -n 1
tfmini status
listener distance_sensor -n 1
listener battery_status -n 1
```

## 本次验收结果

| 验收项 | 本次证据和边界 |
| --- | --- |
| 无显示器 | 拔 HDMI 后重启，独立 VNC 通过 SSH 隧道成功；Windows RDP 也已验证能进入 XFCE |
| Wi‑Fi | 在实验室网络时开热点自动切过去，关热点后回实验室；个别 SSH/RDP 超时后恢复，根因未确定 |
| 编译 | 两个包编译成功；不用再重复其他教程中的全套编译流程 |
| 串口与 ROS 2 | UART4 本机回环通过；TELEM2 在 115200 建立 DDS 会话，收到新状态；已运行会话中收到 401 回执 |
| 开机与冷启动 | 两端自启已验证，曾在双方断电重上电后收到新状态，也有一次程序都启动但未连接；**同一次冷启动的双向复验尚无记录** |
| 传感器与飞行 | 光流、雷达已有新消息；光流运动响应、雷达量程内读数、持续通信负载与飞行前检查尚未完整验收；未测试电机 |

## 附录：安装脚本实际执行的构建步骤

主线 A3 已经执行了这些工作。以下用于核对流程、看日志或在同一环境手工复现，已出现 `PX4 BUILD READY` 的板子不用再跑。前提是 A3 的 `base`、`ros` 已完成，`rosdep` 已有可用缓存。请以普通用户编译；只有安装 APT 依赖需要 `sudo`。

```bash
sudo apt-get install -y ros-humble-fastrtps ros-humble-fastcdr libspdlog-dev
source /opt/ros/humble/setup.bash
mkdir -p ~/ros2_px4_ws/src
cd ~/ros2_px4_ws/src
# 仅在相应源码目录还不存在时克隆：
git clone --depth 1 --branch release/1.17 https://github.com/PX4/px4_msgs.git
git clone --depth 1 --branch v2.4.2 https://github.com/eProsima/Micro-XRCE-DDS-Agent.git
cd ~/ros2_px4_ws
rosdep install --from-paths src --ignore-src --rosdistro humble -r -y \
  --skip-keys 'fastcdr fastrtps'
CMAKE_BUILD_PARALLEL_LEVEL=1 MAKEFLAGS=-j1 colcon build --executor sequential --cmake-args \
  -DCMAKE_BUILD_TYPE=Release -DUAGENT_SUPERBUILD=OFF \
  -DUAGENT_USE_SYSTEM_FASTDDS=ON -DUAGENT_USE_SYSTEM_FASTCDR=ON \
  -DUAGENT_USE_SYSTEM_LOGGER=ON -DUAGENT_P2P_PROFILE=OFF -DUAGENT_BUILD_TESTS=OFF
source install/setup.bash
```

这里使用 Humble 已安装的 DDS 库，避免再编一套 Fast DDS。此过程编的是消息包和 Linux Agent，不是 PX4 固件。若已经有源码，先用 `git status`、`git branch --show-current` 或 `git describe --tags --exact-match` 核对正确版本；不要覆盖已有改动。

| 要找的配置 | 本次板子上的位置 |
| --- | --- |
| Netplan 与 NetworkManager | `/etc/netplan/90-radxa-networkmanager.yaml`、`/etc/NetworkManager/system-connections/radxa-*.nmconnection` |
| 热点优先 | `radxa-hotspot-prefer.timer`、`radxa-hotspot-prefer.service`；脚本在 `/usr/local/sbin/radxa-hotspot-prefer` |
| XFCE / VNC / xrdp | `radxa-desktop.service`、`~/.vnc/Xtigervnc-session`、`/etc/xrdp/xrdp.ini` |
| 打开终端时自动加载 ROS 环境 | `~/.bashrc`；加载 `/opt/ros/humble/setup.bash` 和 `~/ros2_px4_ws/install/setup.bash` |
| 开机自动加载 Agent 环境 | `/etc/systemd/system/radxa-px4-agent.service` 的 `ExecStart` |
| UART4 设备树 | `/etc/default/u-boot`；生成到 `/boot/extlinux/extlinux.conf` |

## 参考资料与发布说明

- 镜像和 overlay：[Ubuntu Rockchip v2.4.0](https://github.com/Joshua-Riek/ubuntu-rockchip/releases/tag/v2.4.0)、[镜像项目 Ubuntu 22.04 LTS 说明](https://github.com/Joshua-Riek/ubuntu-rockchip/wiki/Ubuntu-22.04-LTS)。
- 接口：[Radxa ZERO 3 硬件接口](https://docs.radxa.com/en/zero/zero3/hardware-design/hardware-interface)、[Holybro Pixhawk 6C 端口](https://docs.holybro.com/autopilot/pixhawk-6c/pixhawk-6c-ports)。
- 网络和远程桌面：[NetworkManager 连接设置](https://networkmanager.dev/docs/api/latest/settings-connection.html)、[Ubuntu Jammy TigerVNC 手册](https://manpages.ubuntu.com/manpages/jammy/en/man1/tigervncserver.1.html)。
- 软件链：[ROS 2 Humble / Ubuntu 安装](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)、[PX4 v1.17 uXRCE-DDS](https://docs.px4.io/v1.17/en/middleware/uxrce_dds)、[PX4 v1.17 参数表](https://docs.px4.io/v1.17/en/advanced_config/parameter_reference)、[px4_msgs release/1.17](https://github.com/PX4/px4_msgs/tree/release/1.17)、[Agent v2.4.2](https://github.com/eProsima/Micro-XRCE-DDS-Agent/tree/v2.4.2)。

若新建 GitHub 仓库，把本目录中的 `README.md`、`操作详解.md`、`故障排查.md`、`实测过程与验收.md`、`.gitignore` 和 `ubuntu22-setup/` 放在仓库根目录。ROS APT 包和 rosdep 快照由读者从上游下载。不要上传整张 SD 卡、真实 `user-data` / `network-config`、含密码的 Wi‑Fi 配置、私钥、完整飞控参数、原始日志或未打码的截图。已有飞控参数备份用于本机回退，其他读者应各自导出自己的参数。
