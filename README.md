# 财富流通中心

把时间当作本金，把学习变成投资，把知识沉淀成可复利的长期财富。

## 为什么叫「财富流通中心」

这个名字来自一个核心想法：
**希望把“时间财富”流通成只会复利的“知识财富”。**

时间花掉就回不来，但知识会积累、会复用、会放大，越学越值钱。

## 功能概览

- 新建学习任务时自动写入苹果日历
- 支持 5 类学习：课程学习、复习巩固、技能拓展、知识库搭建、做作业
- 完成任务后获得 XP 与财富值，并累计连续学习天数（streak）
- Web 端支持任务创建、完成、修改、删除
- 复习快捷标签：一键按历史时长创建下一次复习任务
- 学习可视化：最近 1 个月学习时长、学习类型分布、每周学习大纲
- 任务与倒计时表：统一查看任务状态、日历同步状态和剩余时间
- 桌面悬浮提醒（系统级）：显示本次学习剩余、未学习时长、当前任务、下次学习

## 运行环境

- macOS（用于苹果日历写入）
- Python 3.9+

## 快速开始

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 wealth_center_web.py
```

启动后访问：`http://127.0.0.1:4318`

首次写入日历时，macOS 可能弹出授权窗口，选择允许即可。

## CLI 模式

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 study_game.py
```

## 桌面悬浮提醒

先启动 Web 服务，再启动悬浮窗：

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 wealth_center_web.py
python3 desktop_study_overlay.py
```

## 项目结构

- `wealth_center_web.py`：Web 服务与 API
- `web/index.html`：前端页面
- `study_game.py`：CLI 版本
- `desktop_study_overlay.py`：系统级桌面悬浮提醒
- `study_state.json`：本地数据文件

## 数据说明

- 运行数据默认保存在 `study_state.json`
- 数据异常时会自动备份损坏文件并重新初始化
