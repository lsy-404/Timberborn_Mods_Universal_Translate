# 客户端工作

## 计划
- 检查模型请求和响应约束、费用估算与预算预留。
- 调整 pearl 调用及响应处理，并为实际后端差异验证。
- 执行相关离线测试，记录结果和限制。

## 发现与进展
- 当前配置和默认值强制指定具体模型，响应校验拒绝别名映射出的模型；改为 pearl 并接受服务端模型标识。保留输出完成状态及格式校验，计入可见的 reasoning token。
- API 为 chat completions；凭据可由 --api-token、LLM_TOKEN、OPENAI_API_KEY 或本地 secrets.LLM_TOKEN 提供，配置文件可由 --config 指定。
- 原固定费率按当前目录价格估算，不代表 alias 未来可变价格；累计密钥用量可读取但不提供单个请求前的硬预算承诺。正在确认计费策略。
- 创建 test/.venv 后，PYTHONUTF8=1 下网关测试 7 项、生产测试 19 项、名字池测试 15 项通过。默认 Windows 编码导致 3 个生产测试错误；使用 UTF-8 模式通过。宽泛测试中 artifact 脚本依赖 POSIX O_DIRECTORY，Windows 不支持。

- 用量实现改用 gateway status 的动态 quota_per_unit 和 token usage 累计值差，保留两路并发，允许在途预占回落；失败、单位无效或无效减少时停止，最终等待 in-flight 归零重新读取。客户端阈值允许在途超额，key 其他进程的消耗也会计入差值。
- 核算测试 8 项通过，覆盖单位换算、失败、双并发和预占回落。旧测试仍使用已删除的固定价格字段和未 mock 的计费请求，需要同步修订。提交 5448ddd、5f68aaf。

- 测试契约已更新：production 16、name pool 15、format context 16、gateway accounting 8 通过；reasoning_tokens 可为空但不接受非法类型。旧 recovery/gateway 测试由协作代理处理。提交 3b8439a、6243c77、f17a6de。

- 独立冷 tokenizer 缓存时全局 GET 替身误拦截词表下载；为五个受影响 suite 改为只模拟网关 status 和 token usage，其他主机使用原始 GET。新增无需下载的字节分流回归，生产测试全新缓存实测 17 项通过；其余五组共 68 项通过。

- 新验证在首次提供商请求前因用量响应封装不匹配而停止；完整控制器返回 usage 的 code=true 和 object=token_usage，status 仍为 success=true。客户端与全部测试桩已按各自契约修复，错误标记、对象及无效计数均拒绝；六组离线测试共 86 项通过。

- 真实 run 首次 POST 因提示词文本块含网关不支持的私有缓存字段被拒，供应商无调用。已在提示词生成处移除该字段，保留全部文本与顺序；捕获的实际 POST 仅使用标准文本块和预期请求字段，格式、术语与上下文边界断言保留。六组离线测试共 86 项通过。
