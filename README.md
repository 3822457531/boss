# 直聊小蜜 · 1.0（卡密双端）

客户机本机工作台 + 服务器卡密总控。客户机须输入卡密激活；模型 / Jev 密钥由总控加密下发。

## 一、服务器总控台（发卡密 / 配模型 / 发版）

1. 准备 MySQL，复制环境变量：

```powershell
copy .env.server.example .env.server
# 编辑 LICENSE_MYSQL_DSN / LICENSE_ADMIN_PASSWORD
```

2. 安装依赖并启动：

```powershell
pip install -r requirements.txt
python run_server.py
```

默认：http://0.0.0.0:8787/  
管理员密码见 `LICENSE_ADMIN_PASSWORD`（首次初始化必须自行设置强口令，未设置则总控台拒绝启动）。

3. 浏览器登录总控 → **模型与判断**（填 LLM/Jev Key）→ **卡密生成** → 把卡密发给客户。

## 二、客户机（直聊小蜜）

本机需已安装 **Google Chrome**（自动化用它打开招聘网页）。自动化引擎已随安装包侧车捆绑，**一般无需再装 Node / npm**。

### 开发态启动

```powershell
pip install -r requirements.txt
python run_admin.py
# 可选：python run_admin.py --license-server https://your-server:8787
```

打开 http://127.0.0.1:8765/ → 输入卡密激活（总控地址默认见 `LICENSE_SERVER_DEFAULT` / `--license-server`）。

### 对话引擎 2.0

自动回复已接入会话阶段 / 已说清单 / 本轮目标 / 近句去重（`src/dialog_v2.py`）。  
可在 `config/policy.yaml` 调整：

```yaml
dialog_v2:
  enabled: true
  dedup_threshold: 0.78
```

本地调试可跳过卡密：

```powershell
$env:LICENSE_BYPASS="1"; python run_admin.py
```

### 打包安装包（Windows）

1. 安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)  
2. 执行：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.2
```

产出：`dist\installer\ZhiLiaoXiaoMi-Setup-1.0.2.exe`（内含 `runtime\`）  
到总控 **客户端发版** 上传该文件；客户机概览页可「检查更新」。

开发态若要用内置侧车：先跑一遍 build（至少生成 `runtime\`）。

打包时必须用 `-LicenseServer` 指定你自己的总控地址（不指定则内置占位地址）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.3 -LicenseServer https://your-license-server.example.com
```

## 三、客户机首次流程（激活后）

1. 安装 Google Chrome（概览页可一键打开下载）  
2. 确认模型已由总控下发（概览状态）  
3. **打开扫码登录**  
4. **职位与知识库**：同步职位 / 编辑 FAQ  
5. 总控台验证「模拟聊天 / 批量测试」→ 客户机开自动回复  

## 能力摘要

| 能力 | 说明 |
|------|------|
| 卡密授权 | 总控发卡；客户机激活 + 心跳；吊销即锁门 |
| 密钥下发 | AES 加密下发 LLM/Jev，不写本机 `.env` 明文 |
| 多职位 FAQ | 公共 + 职位专属覆盖 |
| 在线更新 | 总控上传 Setup；客户机检查版本 / 强制更新 |
| 安装包 | PyInstaller + Inno Setup（含侧车 runtime） |

## 硬性约束

- 客户机仅绑 `127.0.0.1`；总控可公网  
- API Key 不下发浏览器明文；日志脱敏  
- `not-fit` 不可自动；不破解验证码  
