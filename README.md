# lobsterai2api（Python + FastAPI）

参考 `../lobsterai2api1` Go 版本重构的 LobsterAI OpenAI 兼容桥接服务。

目录按职责分层：

```text
public/favicon.ico           # 站点图标
app/                         # 应用包
├── main.py                  # FastAPI / Vercel 入口
├── api/                     # HTTP 适配层
│   ├── dependencies.py      # 请求依赖与鉴权
│   └── routes/              # OpenAI、状态、健康、签到、积分路由
├── application/             # 应用用例层
│   ├── container.py         # 依赖组装
│   ├── chat.py              # 聊天请求编排
│   ├── models.py            # 模型目录用例
│   ├── checkin.py           # 签到 + 余额刷新 + 解冻用例
│   ├── scheduler.py         # 本地进程内定时触发用例
│   ├── failure_policy.py    # 上游错误后的账号状态策略
│   ├── response_format.py   # OpenAI Responses 格式转换
│   └── sse.py               # SSE 聚合
├── config/                  # 配置层
│   └── settings.py          # pydantic-settings 配置与环境变量
├── domain/                  # 领域层
│   ├── auth.py              # 账号凭证模型
│   ├── persistence.py       # 账号持久化协议
│   └── account_pool.py      # 账号选择、冷却和状态持久化
└── infrastructure/          # 外部系统适配层
    ├── account_store.py     # Redis 账号与状态存储
    └── upstream.py          # LobsterAI HTTP 客户端
```

路由只负责 HTTP 协议，聊天鉴权由 API 中间件统一处理；非 Chat 路由统一返回 `{code, message, data}`，异常由全局异常处理器统一转换；应用层负责业务流程；领域层不依赖 FastAPI；基础设施层负责 Redis 和上游网络访问。

账号凭证写入 Redis Hash `{prefix}:auths`，账号池状态写入 Redis String `{prefix}:data`。签到接口为 `GET|POST /checkin`（兼容 `/admin/checkin`），积分查询接口为 `GET /credits`（兼容 `GET /credit`），只有聊天接口 `/v1/chat/completions` 和 `/v1/responses` 使用 Bearer API Key 鉴权，其他接口不鉴权。除 Chat 外，接口成功响应统一为 `{"code":0,"message":"success","data":...}`。签到会通过活动 Slot、Context 和 `actions/check_in` 接口完成每日签到，解析奖励积分后继续刷新余额和解冻账号。

## 特性

- FastAPI + httpx 异步 HTTP 服务
- 多账号池：按积分优先选择健康账号
- 账号凭证与运行状态均存储在 Redis
- 余额不足/限流/连续错误冷却
- access token 临近过期自动刷新
- `/v1/chat/completions` 支持 OpenAI Chat Completions 非流式聚合和 SSE 流式透传
- `/v1/responses` 支持 OpenAI Responses API 的 `response` 格式和流式事件
- `GET /` 返回服务名称与版本
- `/v1/models`、`/status`、`/healthz` 兼容参考项目
- `/credits`（兼容 `/credit`）查询所有账号当前积分
- `/auth/login`、`/auth/callback` 浏览器登录并添加账号
- Vercel 部署：`vercel.json` 将全部请求转发到 FastAPI，并用 Cron 在 UTC 01:00、13:00 触发签到

## 安装与运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install .
cp .env.example .env
# 按需编辑 .env
# LB2A_REDIS_URL 指向 Redis，auths 与 data 都写到 Redis
# LB2A_MAX_ACCOUNT_RETRIES 控制聊天请求最多尝试的账号数
# 未指定 stream 时默认使用流式响应；显式 stream=false 可关闭
uvicorn app.main:app --host 0.0.0.0 --port 8367
```

配置通过项目根目录的 `.env` 文件或环境变量加载，模板见 `.env.example`；环境变量优先于 `.env`。监听地址和端口由 Uvicorn 的 `--host`、`--port` 参数控制。登录入口通过 `LB2A_LOGIN_PORTAL` 配置，默认是 `https://lobsterai.youdao.com`；回调地址根据当前请求地址自动生成。上游 API 地址默认是 `https://lobsterai-server.youdao.com`。`LB2A_API_KEY` 非空时，仅聊天接口要求 `Authorization: Bearer <api_key>`。本地进程内定时任务小时列表使用 JSON 数组格式，例如 `LB2A_CHECKIN_HOURS=[9, 21]`。部署到 Vercel 时不启动进程内定时器，改由 `vercel.json` 的 Cron 以 GET `/checkin` 触发；Cron 使用 UTC，默认 `0 1,13 * * *` 对应北京时间 09:00 与 21:00。

## 接口示例

```bash
curl http://127.0.0.1:8367/
curl http://127.0.0.1:8367/v1/models
# 打开返回的 login_url，登录完成后会自动添加账号
curl http://127.0.0.1:8367/auth/login
# 打开返回的 login_url 完成登录，回调成功后会写入 Redis `{prefix}:auths`
curl -X POST http://127.0.0.1:8367/checkin
curl http://127.0.0.1:8367/v1/responses \
  -H 'Authorization: Bearer ***' -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-pro","input":"你好","stream":false}'
curl http://127.0.0.1:8367/v1/chat/completions \
  -H 'Authorization: Bearer ***' -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

账号文档同时兼容参考项目的嵌套格式 `{auth, account}` 与扁平格式。
