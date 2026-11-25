---
id: docs.walkthrough.ui-contract
title: "UI 契约与重做指南"
summary: "控制器与视图的语义边界、FakeView 测试范式和未来重做 UI 的接入步骤。"
type: reference
kind: note
status: active
owner: "@simiely"
topics: [quality]
source_type: doc
source: "docs/walkthrough/ui-contract.md"
last_reviewed: 2026-11-25
related: []
---

# UI 契约与重做指南

这份文档说明控制器和视图之间的边界。目标是：**以后重做 UI 不需要再改业务控制器**。

## 1. 三层结构

```text
controller mixin（brconsole/gui/*_controller.py）
    ↓ 只调用语义方法
ViewProtocol（brconsole/view_contract.py，无 tkinter）
    ↓ 两个实现
TkView（brconsole/gui_view_new.py）      FakeView（brconsole/view_contract.py）
```

- 控制器禁止 import `tkinter`，也不允许再新增 `self.v_*` 直连。
- 状态变量与业务回调都以名字注册在 `VARIABLES` / `CALLBACKS`。
- `FakeView` 把每次调用记进 `calls`，单测可直接断言"界面收到了什么"。

## 2. 当前实现状态

- `App._build()` 只构造 `TkView(self, root)`；旧的 `BRC_OLD_UI` 与 `gui_view.py` 已删除。
- `TkView.build()` 仍会在 `App` 上挂一批旧控件别名，控制器方法体尚未全部迁移到 `self.view.*`。这是为了保持行为不变的**过渡兼容层**，不是新代码应该继续使用的 API。
- 新写的控制器代码必须走 `ViewProtocol` 的语义方法，例如：

```python
self.view.set("blend", path)
text = self.view.get("output")
self.view.set_text("status", "正在渲染")
self.view.append_log([line])
```

## 3. FakeView 单测范式

```python
from brconsole.view_contract import FakeView

view = FakeView()
view.set("blend", "a.blend")
view.set_queue([{"id": "q1"}], "q1")
assert view.get("blend") == "a.blend"
assert view.queue_selection() == (0,)
```

契约测试文件：`tests/test_view_contract.py`。

## 4. 换 UI 库时要做什么

1. 新建一个实现 `ViewProtocol` 的类（例如 `QtView`）。
2. 在 `VARIABLES` / `CALLBACKS` 中补齐新 UI 需要的名字；不要改控制器语义方法。
3. 用 `FakeView` 先跑通控制器单测，再接真实 UI。
4. 真窗口验收仍要跑 `python main.py --demo` 与 `tools/capture_screen.py --list`。

## 5. 已知兼容债务

- `gui_view_new.py` 的 `_LabelProxy` / `_StatusProxy` / `_CheckProxy` / `_ComboProxy` 与隐藏 Label 仍在服务旧控制器方法体；等这些方法逐个迁到 `self.view.*` 后即可删除。
- `uikit.QueueList.see()` 是语义上的空操作：左栏卡片区不滚动，没有"滚动到可见"的需求。
