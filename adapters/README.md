# adapters/ —— 只放接口与示例

这个目录随 collab-wf 发布，只包含：
- `base.py`：通道无关的适配器基类（参数解析、动作日志、退出码、约定输出格式）；
- `example_echo.py`：用本地文件模拟通道的示例适配器，供自检与参照。

**具体通道的适配器不放在这里。** 它们放在工具目录之外（例如 `~/.workbuddy-ai/adapter/`），在 `local/transport.json` 中这样指向：

```json
{"module": "aimonkey", "module_path": "~/.workbuddy-ai/adapter", "log": "…", "rate": {"window_s": 600, "max_sends": 8}}
```

wf 以 `python -m <module> <动作> …` 运行它，并把 `module_path` 与工具目录加入 PYTHONPATH，所以外部适配器可以 `from adapters.base import Adapter, main`。
放在工具目录内的通道适配器会随 `wf bootstrap` 与 `self-update` 一起被打包和覆盖，`wf transport` 会对此报警。约定全文见 docs/TRANSPORT.md。
