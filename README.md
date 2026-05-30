# Webshell Detector v7.8 代码分析（安全测试工具 README 风格）

## 1. 工具用途（它在安全领域做什么）

这是一个 **基于字典爆破的 WebShell 自动化检测器**。

主要用途：

* 批量检测网站是否存在已知 WebShell
* 扫描常见上传目录中的可疑 PHP 文件
* 检测：

  * WSO Shell
  * FilesMan
  * B374K
  * C99
  * R57
  * IndoXploit
  * Priv8 Shell
  * 以及各种 PHP WebShell 变种

属于：

> WebShell Discovery Scanner（WebShell发现工具）

而不是：

* 漏洞扫描器
* RCE利用工具
* WebShell管理器

它本质上是在做：

```text
目录字典
     +
文件名字典
     ↓
构造URL
     ↓
发送HTTP请求
     ↓
内容分析
     ↓
风险评分
     ↓
输出疑似WebShell
```

---

# 2. 工作流程

## 输入

### directories.txt

```txt
https://site.com/upload
https://site.com/uploads
https://site.com/images
```

### dictionary.txt

```txt
shell.php
cmd.php
wso.php
b374k.php
up.php
```

---

## URL生成

程序会组合：

```python
safe_url(base, filename)
```

例如：

```text
https://site.com/upload
+
wso.php
```

生成：

```text
https://site.com/upload/wso.php
```

---

## 请求发送

使用：

```python
aiohttp
```

异步请求：

```python
GET https://site.com/upload/wso.php
```

---

## 内容过滤

先过滤：

### Content-Type

只允许：

```python
text/html
text/plain
application/xhtml+xml
```

否则直接丢弃。

---

### 响应大小

最大：

```python
MAX_RESPONSE_SIZE = 2MB
```

超过直接丢弃。

---

### 低信息页面

过滤：

```python
len(content) < 280
```

或者：

```python
换行太少
```

避免：

```html
404
Not Found
```

之类页面。

---

## 错误页过滤

利用：

### MD5 Hash

```python
hashlib.md5(content[:2600])
```

记录同主机常见页面。

如果：

```python
同Hash出现 >= 5次
```

认为：

```text
统一404页
统一错误页
```

跳过。

---

### 长度过滤

记录：

```python
页面长度
```

如果：

```python
±30字节内
出现 >=5次
```

认为：

```text
模板错误页
```

跳过。

---

## WebShell识别

进入：

```python
calculate_risk()
```

评分。

---

# 3. 关键模块解析

---

## 模块1：异步扫描引擎

核心：

```python
asyncio
aiohttp
```

实现：

```python
Producer
 ↓
Queue
 ↓
Worker
```

架构。

---

## 模块2：URL去重

```python
self.seen_urls
```

防止：

```text
重复扫描同URL
```

---

## 模块3：WAF识别

函数：

```python
is_waf_or_blocked()
```

识别：

```text
Cloudflare
Captcha
Sucuri
```

以及：

```python
非200/301/302
```

直接过滤。

---

## 模块4：指纹识别

检测：

```python
CRITICAL_FINGERPRINTS
```

包含：

```python
wso
filesman
b374k
c99
r57
sym
indoxploit
priv8
```

例如页面包含：

```html
WSO 4.2
```

立即加分。

---

## 模块5：危险函数检测

检测：

```python
system(
exec(
passthru(
shell_exec(
eval(
assert(
base64_decode(
gzinflate(
```

这些都是：

### WebShell高危特征

---

## 模块6：组合检测

### eval + base64

```php
eval(base64_decode(...))
```

加：

```python
+40
```

---

### gzinflate + base64

```php
gzinflate(base64_decode(...))
```

加：

```python
+35
```

属于：

```text
混淆WebShell
```

典型特征。

---

### PHP上下文

检测：

```php
<?php
```

如果同时：

```python
多个危险函数
```

额外加：

```python
+35
```

---

## 模块7：上传器检测

识别：

```html
<input type="file">
```

或者：

```text
upload file
uploader
file manager
```

很多 WebShell 自带文件上传功能。

---

# 4. 风险评分机制

## 指纹

```python
25 × 数量
```

---

## 危险函数

```python
28 × 数量
```

---

## 特殊组合

```python
eval + base64
```

+40

---

## 混淆

```python
gzinflate + base64
```

+35

---

## 最终等级

### CRITICAL

```text
80-100
```

---

### HIGH

```text
65-79
```

---

### SUSPICIOUS

```text
62-64
```

默认。

---

### LOW

```text
<62
```

---

# 5. 命令行参数说明

## 必选

### -d

目录列表

```bash
-d directories.txt
```

---

### -w

文件名字典

```bash
-w shells.txt
```

---

## 可选

### -o

输出文件

```bash
-o result.txt
```

默认：

```text
results.txt
```

---

### --min-score

最低风险分

```bash
--min-score 70
```

默认：

```text
62
```

---

### -c

Worker数量

```bash
-c 300
```

默认：

```text
100
```

---

### --global-limit

全局请求上限

```bash
--global-limit 500
```

默认：

```text
180
```

---

### --allow-redirect

允许跳转

```bash
--allow-redirect
```

---

### --disable-error-filter

关闭错误页过滤

```bash
--disable-error-filter
```

---

### --json

输出JSON

```bash
--json
```

生成：

```text
findings.jsonl
```

---

# 6. 输入输出示例

## 输入

directories.txt

```txt
https://target.com/upload
```

shells.txt

```txt
wso.php
cmd.php
up.php
```

---

## 执行

```bash
python detector.py \
-d directories.txt \
-w shells.txt \
-c 300 \
--json
```

---

## 输出

results.txt

```txt
[CRITICAL] 100 https://target.com/upload/wso.php
[HIGH] 78 https://target.com/upload/up.php
```

---

found_webshells.txt

```txt
https://target.com/upload/wso.php
https://target.com/upload/up.php
```

---

findings.jsonl

```json
{
 "url":"https://target.com/upload/wso.php",
 "score":100,
 "risk":"CRITICAL"
}
```

---

# 7. 性能与并发机制

这是本工具最大的亮点。

---

## asyncio

采用：

```python
asyncio
```

不是：

```python
threading
```

因此：

```text
内存更低
切换更快
更适合百万级URL
```

---

## aiohttp连接池

```python
TCPConnector(
 limit=600
)
```

最大：

```text
600连接
```

---

## 全局限流

```python
global_semaphore
```

控制：

```text
总并发
```

默认：

```text
180
```

---

## 单站限流

```python
host_semaphores
```

每个域名：

```python
Semaphore(4)
```

即：

```text
同站最多4并发
```

避免打爆目标。

---

## Producer / Consumer

```text
Producer
 ↓
Queue
 ↓
100 Workers
```

标准高性能架构。

---

# 8. 如何部署和使用

## Step 1

安装 Python

```bash
python3 --version
```

建议：

```text
Python 3.10+
```

---

## Step 2

安装依赖

```bash
pip install -r requirements.txt
```

---

## Step 3

准备目录字典

```txt
https://site.com/upload
https://site.com/uploads
```

---

## Step 4

准备文件名字典

```txt
shell.php
cmd.php
wso.php
```

---

## Step 5

运行

```bash
python detector.py \
-d directories.txt \
-w webshell.txt \
-c 300 \
--json
```

---

## Step 6

查看结果

```text
results.txt
found_webshells.txt
critical.txt
high.txt
suspicious.txt
findings.jsonl
```

---

# 9. requirements.txt

根据代码实际依赖：

```txt
aiohttp>=3.10.0
colorama>=0.4.6
```

如果固定版本：

```txt
aiohttp==3.10.5
colorama==0.4.6
```

---

# 风险提示

### 优点

* 异步高速扫描
* 多级误报过滤
* WebShell特征识别较全面
* 支持百万级URL检测
* 支持JSON结果导出

### 缺点

* 只能发现可直接访问的 WebShell
* 无法发现需要密码认证的 Shell
* 无法执行动态行为分析
* 对纯静态关键字检测依赖较大
* 可能误报安全研究文章、代码示例页面
* 对高度定制化、无明显特征的私有 WebShell 检出率有限

### 安全与合规

该工具应仅用于获得授权的资产安全测试。对未经授权的网站进行大规模扫描可能违反目标站点政策或相关法律法规。
