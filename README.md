# 财富流通中心（学习财富游戏）

一个基于苹果日历的自我管理小游戏，核心理念是：**知识就是财富**。

- 创建学习任务时，会自动写入苹果日历
- 支持 5 类学习：课程学习、复习巩固、技能拓展、知识库搭建、做作业
- 完成任务后可获得经验与财富值（而不是简单打卡）
- 支持连续学习天数（streak）和升级
- 支持学习时间可视化（最近 7 天 + 类型分布 + 周目标进度）
- 自动周目标追踪：每周课程门数目标（2门）+ 学习时长目标（600分钟）
- 智能提醒：距离周末较近时会自动提示还差多少课程/时长
- 网页端支持“复习快捷标签”：点标签可一键创建下一次复习
- 任务创建采用“先保存后异步写日历”，减少创建时卡顿
- 网页端任务列表支持：完成、修改、删除
- 不同学习类型使用不同主题色，不同课程自动分配独立颜色
- 新增每周学习大纲（周一到周日）
- 新增“学习倒计时表”，实时显示距离开始/结束的剩余时间

## 运行方式

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 study_game.py
```

首次写入日历时，macOS 可能弹出授权窗口，选择允许即可。

## 网页界面（本地部署）

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 wealth_center_web.py
```

启动后访问：`http://127.0.0.1:4318`

## 桌面悬浮提醒（系统级，不在网页内）

先启动 Web 服务：

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 wealth_center_web.py
```

再启动桌面悬浮窗：

```bash
cd "/Users/yuandai/Documents/New project/study-time-game"
python3 desktop_study_overlay.py
```

说明：
- 小窗常驻最前，可拖动位置，按 `Esc` 或点 `×` 关闭
- 实时显示：本次学习剩余时间、未学习时长、当前任务、下次学习

## 玩法

1. 选择 `1`：新建学习任务（并同步到苹果日历）
2. 选择 `2`：完成任务，领取奖励
3. 选择 `3`：查看任务列表
4. 选择 `4`：查看角色状态
5. 选择 `5`：查看学习时间可视化
6. 选择 `6`：查看本周目标提醒（包含建议）
7. 选择 `7`：退出

## 数据文件

- 游戏数据保存在：`study_state.json`
- 如果文件损坏，程序会自动备份为 `study_state.broken.json` 并重新初始化
