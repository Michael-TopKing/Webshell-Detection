## 1️⃣ 安装依赖

```bash
git clone https://github.com/Michael-TopKing/Webshell-Detection.git
cd Webshell-Detection
pip3 install -r requirements.txt
```

## 2️⃣ 准备文件

```
directories.txt
dictionary.txt
```

## 3️⃣ 运行

```bash
python detector.py \
-d directories.txt \
-w webshell.txt \
-c 300 \
--json
```

## 4️⃣ 查看结果

```text
results.txt
found_webshells.txt
critical.txt
high.txt
suspicious.txt
findings.jsonl
```
