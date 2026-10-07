# scm_admin —— 运营后台

Flask 写的运营后台（默认 `:5000`），给运营／主管看坐席名下的客户、会话记录、订单与
标签体系。**它自己不存数据**：所有页面都经 LangGraph 平台的 Store 读取，客户端也
只跟平台服务打交道。

## 起法

必须先起平台服务，再起后台：

```bash
cd ../scm_agent && ./run_scm_agent     # 平台服务，:2024
cd ../scm_admin && ./run_scm_admin     # 本后台，:5000
```

打开 <http://127.0.0.1:5000> 会自动跳到登录页，账号取 `.env` 里的
`ADMIN_USER_ID` / `ADMIN_PASSWORD`（与平台账号是两套，见下）。

> 只想看智能体效果的话不用起这个：仓库根目录 `python quickstart.py` 离线就能跑通
> 全部链路，坐席工作台也在平台服务里（`/scm/workbench/`）。

## 页面

| 路径 | 内容 |
|---|---|
| `/employees` | 坐席列表 |
| `/employee_customers/<user_id>` | 某坐席名下的客户 |
| `/customer_detail/<follow_user_id>/<external_id>` | 客户详情（标签、画像） |
| `/chat_records/<user_id>/<external_id>` | 单客户会话记录 |
| `/kf_records/<external_id>` | 客服会话记录 |
| `/customer_orders/<union_id>` | 客户订单 |
| `/tags` | 标签体系 |

每个页面配一个 `/api/...` 接口（返回 JSON），页面用 jQuery 拉数据渲染。

## 配置（`.env`）

| 变量 | 说明 |
|---|---|
| `SCM_SERVER_URL` | 平台服务地址，默认 `http://127.0.0.1:2024` |
| `SCM_USER_ID` / `SCM_PASSWORD` | 换令牌用：后台拿它 POST `{SCM_SERVER_URL}/scm/get_token`。**必须与 `scm_agent` 的 `DEMO_USER_ID` / `DEMO_USER_PASSWORD` 一致**，否则登录不上、或每页提示「获取token失败」 |
| `ADMIN_USER_ID` / `ADMIN_PASSWORD` | 本后台自己的登录账号（与平台账号是两套） |
| `FLASK_SECRET_KEY` | session 签名密钥。不填则每次启动随机生成 —— 能跑，但重启后要重新登录 |

复制 `.env.example` 为 `.env` 后填写。

`scm_store_api/scm_store_api.py` 是访问平台 Store 的客户端封装。注意 `scm_client/`
下有一份同名文件，是它的**超集**（多出灌演示数据用的写接口：`upsert_external_user`、
`upsert_tags_setting` 等）—— 两份目前各自维护，后台这份只保留了读接口。
