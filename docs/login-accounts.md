# 登录账户识别（昵称 / profileId / 小红书号）

> 多 profile 场景下用「昵称 + 小红书号」区分账户。配套 [container-env.md](container-env.md)。

## 1. 身份来源（零导航）
- **www（浏览页）**：读 `window.__INITIAL_STATE__.user.userInfo._rawValue` → `userId`(=profileId)、`nickname`、`redId`（小红书号）。
- **creator（创作平台）**：DOM `.personal .base .text .account-name`（昵称）+ `.others.description-text` 里的「小红书账号: NNN」；**取不到 = 未登录/未认证，忽略**。

## 2. 关联落盘：`data/accounts_meta.json`（data 卷）
```json
{ "default": {
    "profile_id": "5fb29e90000000000101d045",
    "nickname": "快马", "red_id": "1072616531",
    "creator": { "nickname": "快马", "red_id": "1072616531" } } }
```

## 3. 接口
- `GET /api/login/www/status` → `{logged_in, nickname, profile_id, red_id, account}`
- `GET /api/login/status` → `{logged_in, nickname, red_id}`（creator）
- `GET /api/login/accounts/meta` → 各账户 `{profile_id,nickname,red_id,creator}`

## 4. Admin UI
- **主界面**登录按钮旁：`浏览:快马·1072616531  创作:快马·1072616531`；浏览/创作小红书号不一致时**标红**。
- **登录页 `/login`**：账户下拉**用昵称**区分（读 `accounts/meta`，无昵称回退别名/账户名）；含「切换到此账户」按钮，切换后若与实际登录的小红书号对不上 → **红色报警**。
