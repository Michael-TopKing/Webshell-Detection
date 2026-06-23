## 1. 工具用途（它在安全领域做什么）

这是一个**异步高并发 Webshell 自动化检测工具**。

主要用于：

* 批量扫描网站目录
* 检测暴露在公网的 PHP Webshell
* 识别常见后门管理界面
* 发现上传器（Uploader）
* 发现文件管理器（FilesMan、WSO 等）
* 输出高危疑似 Webshell URL

适用于：

* 红队资产排查
* 漏洞赏金（Bug Bounty）
* 应急响应
* 蓝队网站巡检
* Webshell 存活检测

---

## 2. 工作流程（从输入到输出）

整体流程：

```text
目录列表
    ↓
字典列表
    ↓
组合URL
    ↓
异步请求
    ↓
内容过滤
    ↓
错误页过滤
    ↓
风险评分
    ↓
结果分类
    ↓
保存文件
```

例如：

目录：

```text
https://site1.com
https://site2.com
```

字典：

```text
shell.php
cmd.php
upload.php
```

生成：

```text
https://site1.com/shell.php
https://site1.com/cmd.php
https://site1.com/upload.php

https://site2.com/shell.php
https://site2.com/cmd.php
https://site2.com/upload.php
```

然后逐个检测。

---

# 3. 核心检测流程解析

---

## 阶段1：URL生成

函数：

```python
safe_url()
```

作用：

```python
https://target.com
+
shell.php
=
https://target.com/shell.php
```

避免：

```python
//
```

重复拼接问题。

---

## 阶段2：去重

```python
self.seen_urls
```

避免：

```text
https://a.com/shell.php
https://a.com/shell.php
```

被扫描两次。

使用：

```python
asyncio.Lock()
```

保证协程安全。

---

## 阶段3：发起HTTP请求

核心：

```python
aiohttp.ClientSession
```

请求：

```python
GET /shell.php
```

随机：

```python
User-Agent
Accept-Language
```

降低被WAF识别概率。

---

## 阶段4：内容类型过滤

只允许：

```python
text/html
text/plain
application/xhtml+xml
```

过滤：

```text
jpg
png
zip
pdf
exe
```

避免浪费资源。

---

## 阶段5：响应大小过滤

```python
MAX_RESPONSE_SIZE = 2MB
```

如果：

```python
len(body) > 2MB
```

直接跳过。

避免：

```text
大日志
数据库导出
超大文件
```

拖垮内存。

---

## 阶段6：低信息页面过滤

```python
if len(content) < 280
```

过滤：

```html
404
403
hello
test
```

减少误报。

---

# 4. 错误页过滤机制（重点）

这是本工具最大的优化之一。

---

## Hash过滤

函数：

```python
is_likely_error_page()
```

计算：

```python
md5(content[:2600])
```

例如：

```html
404 Not Found
```

连续出现：

```text
A
A
A
A
A
```

达到：

```python
count >= 5
```

认为：

```text
统一错误页
```

直接过滤。

---

## 内容长度过滤

新增：

```python
error_page_lengths
```

记录：

```python
len(content)
```

例如：

```text
16322
16320
16318
16323
16321
```

差异：

```python
abs(x - length) < 30
```

达到：

```python
similar >= 5
```

判断：

```text
同一个错误模板
```

过滤。

---

## 为什么能减少误报？

很多网站：

```text
404 页面返回 200
```

例如：

```html
Oops...
Page Not Found
```

但：

```http
HTTP 200
```

传统扫描器：

```text
误认为存在文件
```

这个机制能干掉大量误报。

---

# 5. Webshell识别引擎

---

## 指纹库

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
madspot
priv8
```

这些都是著名Webshell。

---

## 正则规则

```python
CRITICAL_REGEX
```

检测：

```php
system(
exec(
passthru(
shell_exec(
eval(
assert(
base64_decode(
gzinflate(
```

---

## 组合检测

### Eval + Base64

```php
eval(base64_decode(...))
```

加：

```python
+40
```

---

### Gzinflate + Base64

```php
gzinflate(base64_decode(...))
```

加：

```python
+35
```

典型混淆木马。

---

### 多执行函数

```php
system()
exec()
passthru()
```

同时存在：

```python
MULTI_EXEC_PHP
```

加：

```python
+35
```

---

### 上传器检测

发现：

```html
type="file"
upload file
uploader
```

加：

```python
+22
```

---

### 命令执行界面

发现：

```html
<textarea>
```

并包含：

```text
cmd
shell
exec
system
```

加：

```python
+25
```

---

# 6. 风险评分系统

评分范围：

```python
0~100
```

---

## CRITICAL

```text
80+
```

输出：

```text
critical.txt
```

---

## HIGH

```text
65+
```

输出：

```text
high.txt
```

---

## SUSPICIOUS

```text
62+
```

输出：

```text
suspicious.txt
```

---

## LOW

忽略。

---

# 7. WAF检测

函数：

```python
is_waf_or_blocked()
```

检测：

```text
Cloudflare
cf-ray
captcha
sucuri
attention required
```

或者：

```http
403
429
503
```

（实际上代码中是非200/301/302直接过滤）

判定：

```text
被WAF拦截
```

不参与评分。

---

# 8. 并发机制（重点）

这是高性能核心。

---

## 全局并发

```python
self.global_semaphore
```

默认：

```python
180
```

表示：

```text
同时最多180个请求
```

---

## 单主机并发

```python
host_semaphores
```

固定：

```python
4
```

即：

```text
每个域名最多4个请求
```

避免：

```text
把单个站点打挂
```

---

## Worker模型

```python
Queue
+
Worker
```

结构：

```text
Producer
     ↓
 Queue
     ↓
100 Workers
     ↓
check_url()
```

---

## aiohttp异步

使用：

```python
asyncio
aiohttp
```

不是：

```python
requests
```

因此性能远高于传统脚本。

---

# 9. 自适应限速机制

函数：

```python
_adaptive_adjust()
```

每：

```python
8秒
```

统计：

```python
HTTP 200占比
```

若：

```python
>75%
```

提高：

```python
+15
```

并发。

例如：

```text
180
→
195
→
210
```

直到：

```python
global_limit
```

上限。

---

# 10. 输出文件说明

## results.txt

完整结果：

```text
[CRITICAL] 95 https://target.com/shell.php
```

---

## found_webshells.txt

仅URL：

```text
https://target.com/shell.php
```

适合后续导入工具。

---

## critical.txt

高危Webshell。

---

## high.txt

高风险。

---

## suspicious.txt

可疑目标。

---

## findings.jsonl

JSON格式：

```json
{
  "url":"https://target.com/shell.php",
  "score":95,
  "risk":"CRITICAL",
  "matched":["FINGERPRINT×2","REGEX×4"],
  "title":"WSO Shell",
  "time":"22:35:10"
}
```

适合：

```text
ELK
Splunk
SIEM
```

导入分析。

---

# 11. 命令行参数说明

```bash
python detector.py [options]
```

| 参数                     | 说明       |
| ---------------------- | -------- |
| -d                     | 目录列表     |
| -w                     | 文件名字典    |
| -o                     | 输出文件     |
| --min-score            | 最低风险分    |
| -c                     | Worker数量 |
| --global-limit         | 全局并发     |
| --disable-error-filter | 关闭错误页过滤  |
| --allow-redirect       | 允许302跳转  |
| --json                 | 输出JSON   |

示例：

```bash
python detector.py \
-d urls.txt \
-w webshell.txt \
-c 100 \
--global-limit 180 \
--json
```

---

# 12. 输入示例

## urls.txt

```text
https://site1.com
https://site2.com
https://site3.com
```

---

## webshell.txt

```text
shell.php
cmd.php
upload.php
wso.php
```

---

# 13. 输出示例

```text
[CRITICAL] 100 https://site1.com/wso.php

[HIGH] 72 https://site2.com/upload.php

[SUSPICIOUS] 65 https://site3.com/cmd.php
```

---

# 14. requirements.txt

```txt
aiohttp>=3.10.0
colorama>=0.4.6
```

安装：

```bash
pip install -r requirements.txt
```

---

# 15. 优缺点评价

## 优点

✅ asyncio + aiohttp 高并发

✅ Host级限流

✅ Hash错误页过滤

✅ 长度相似过滤

✅ 风险评分系统

✅ JSON输出

✅ 实时统计

✅ 自动去重

✅ Webshell指纹识别

---

## 缺点

### 1. 只使用 GET

没有：

```text
POST检测
Cookie检测
认证绕过检测
```

---

### 2. 无JS渲染

无法检测：

```text
React
Vue
Angular
```

动态页面。

---

### 3. 误报仍存在

例如文章：

```php
eval(base64_decode())
```

教程页面可能被打分。

虽然已经有：

```python
tutorial
example
blog
github
```

降权机制，但无法彻底避免。

---

### 4. 自适应限速有逻辑缺陷

代码只会：

```text
升并发
```

不会：

```text
降并发
```

因此严格来说不是真正的 Adaptive Rate Control。

---

## 综合评价

从安全扫描工具角度看，这个脚本已经属于**中高级 Webshell 批量探测器**。相比普通字典扫描器，它最大的亮点是：

* 双重错误页过滤（Hash + 长度）
* 风险评分引擎
* Webshell 指纹识别
* asyncio 高并发架构

如果用于大规模资产巡检，实际效果会明显优于只判断 HTTP 200 的传统目录扫描脚本。

