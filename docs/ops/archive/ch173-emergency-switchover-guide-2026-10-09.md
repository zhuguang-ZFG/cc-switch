# ch173 嵌入模型故障紧急切换指南

**文档版本**: 1.0  
**最后更新**: 2026-10-09  
**风险等级**: 中  
**影响范围**: OMP 语义搜索功能

---

## 概述

ch173 (muyuan-gongyi) 是 OMP 嵌入模型的唯一来源。当该渠道故障时，语义搜索功能将完全失效。本文档提供紧急切换操作流程。

---

## 故障识别

### 症状
- OMP 记忆检索失败
- 语义搜索无结果
- 日志中出现嵌入模型调用错误

### 快速诊断

```bash
# 运行嵌入模型稳定性检查
python3 scripts/ops/monitor-embedding-stability.py
```

**正常输出**:
```
[OK] mistral-embed-2312: 795ms, 1024d
Summary: 4/4 models OK
Status: ALL STABLE
```

**故障输出**:
```
[FAIL] mistral-embed-2312: timeout
Status: DEGRADED
```

---

## 紧急切换流程

### 步骤 1: 确认故障 (30 秒)

```bash
# 测试所有嵌入模型
python3 scripts/ops/monitor-embedding-stability.py

# 检查 ch173 渠道状态
python3 -c "
import sqlite3
from pathlib import Path
db = Path.home() / '.new-api-local' / 'new-api.db'
conn = sqlite3.connect(str(db))
cur = conn.cursor()
cur.execute('SELECT id, name, status FROM channels WHERE id = 173')
row = cur.fetchone()
print(f'ch{row[0]}: {row[1]}, status={row[2]}')
conn.close()
"
```

**判断标准**:
- `status=1`: 渠道启用，可能是上游故障
- `status=2`: 渠道禁用，需要启用备用方案
- 所有模型 FAIL: ch173 完全不可用

### 步骤 2: 切换到备用嵌入模型 (1 分钟)

#### 方案 A: 切换到同渠道其他模型（如果 ch173 部分可用）

编辑 `~/.omp/agent/config.yml`:

```yaml
mnemopi:
  noEmbeddings: false
  embeddingModel: zg-newapi/codestral-embed-2505  # 备用 1
  # 或
  embeddingModel: zg-newapi/codestral-embed       # 备用 2
```

#### 方案 B: ch173 完全不可用

**无自动故障转移**，需要手动操作：

1. **临时禁用语义搜索**（避免错误日志）:
   ```yaml
   mnemopi:
     noEmbeddings: true  # 临时禁用
   ```

2. **重启 OMP**:
   ```bash
   # 如果使用 systemd
   systemctl restart omp
   
   # 如果使用 Docker
   docker restart omp
   
   # 如果手动运行
   # Ctrl+C 停止，然后重新启动
   ```

3. **记录故障时间**:
   ```bash
   echo "$(date): ch173 故障，已禁用语义搜索" >> ~/.omp/incident-log.txt
   ```

### 步骤 3: 验证切换 (30 秒)

```bash
# 测试 OMP 是否正常启动
omp --version

# 检查日志是否有嵌入模型错误
tail -f ~/.omp/logs/omp.log | grep -i embed
```

**预期结果**:
- 如果禁用语义搜索：无嵌入模型相关错误
- 如果切换到备用模型：嵌入调用成功

### 步骤 4: 通知相关人员

```bash
# 发送通知（如果有配置）
echo "ch173 嵌入模型故障，已切换到备用方案" | mail -s "[OMP] 嵌入模型故障" admin@example.com
```

---

## 恢复流程

### 当 ch173 恢复后

1. **验证 ch173 可用性**:
   ```bash
   python3 scripts/ops/monitor-embedding-stability.py
   ```

2. **恢复默认配置**:
   ```yaml
   mnemopi:
     noEmbeddings: false
     embeddingModel: zg-newapi/mistral-embed-2312  # 恢复默认
   ```

3. **重启 OMP**:
   ```bash
   systemctl restart omp
   ```

4. **验证恢复**:
   ```bash
   # 测试语义搜索
   omp memory search "test query"
   
   # 检查日志
   tail -n 20 ~/.omp/logs/omp.log | grep -i embed
   ```

5. **记录恢复时间**:
   ```bash
   echo "$(date): ch173 恢复，语义搜索已恢复" >> ~/.omp/incident-log.txt
   ```

---

## 备用模型优先级

| 优先级 | 模型 | 延迟 | 维度 | 用途 |
|--------|------|------|------|------|
| 1 | mistral-embed-2312 | 795ms | 1024 | **默认**（最快） |
| 2 | codestral-embed-2505 | 901ms | 1536 | 备用 1 |
| 3 | mistral-embed | 974ms | 1024 | 备用 2 |
| 4 | codestral-embed | 1069ms | 1536 | 备用 3 |

**注意**: 所有备用模型均来自 ch173，如果 ch173 完全不可用，所有模型都无法使用。

---

## 自动化监控脚本

### 创建定时检查任务

```bash
# 添加到 crontab (每 5 分钟检查一次)
(crontab -l 2>/dev/null; echo "*/5 * * * * /usr/bin/python3 /path/to/scripts/ops/monitor-embedding-stability.py >> ~/.omp/logs/embedding-monitor.log 2>&1") | crontab -
```

### 告警阈值

```python
# 在 monitor-embedding-stability.py 中添加告警逻辑
if ok_count < len(EMBEDDING_MODELS):
    # 发送告警
    send_alert(f"嵌入模型故障: {ok_count}/{len(EMBEDDING_MODELS)} 可用")

if avg_latency > 2000:  # 2 秒阈值
    send_alert(f"嵌入模型延迟过高: {avg_latency}ms")
```

---

## 故障场景与应对

### 场景 1: ch173 响应缓慢 (>5 秒)

**症状**: 嵌入调用延迟高，OMP 响应慢

**应对**:
1. 切换到更快的模型（mistral-embed-2312）
2. 检查 ch173 上游状态
3. 考虑临时禁用语义搜索

### 场景 2: ch173 部分模型不可用

**症状**: 某些模型 FAIL，其他 OK

**应对**:
1. 切换到可用模型
2. 更新 `embeddingModel` 配置
3. 记录故障模型，避免使用

### 场景 3: ch173 完全不可用

**症状**: 所有模型 FAIL

**应对**:
1. 禁用语义搜索 (`noEmbeddings: true`)
2. 重启 OMP
3. 等待 ch173 恢复或寻找新渠道

---

## 联系信息

- **NewAPI 管理员**: [填写联系方式]
- **OMP 运维**: [填写联系方式]
- **紧急联系**: [填写联系方式]

---

## 更新历史

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-10-09 | 1.0 | 初始版本 | AI Assistant |

---

## 附录

### 快速命令参考

```bash
# 诊断
python3 scripts/ops/monitor-embedding-stability.py

# 切换配置
vim ~/.omp/agent/config.yml

# 重启 OMP
systemctl restart omp

# 查看日志
tail -f ~/.omp/logs/omp.log | grep -i embed

# 记录事件
echo "$(date): [事件描述]" >> ~/.omp/incident-log.txt
```

### 配置示例

**正常配置**（默认）:
```yaml
mnemopi:
  noEmbeddings: false
  embeddingModel: zg-newapi/mistral-embed-2312
```

**故障配置**（禁用语义搜索）:
```yaml
mnemopi:
  noEmbeddings: true
```

**备用配置**（切换到其他模型）:
```yaml
mnemopi:
  noEmbeddings: false
  embeddingModel: zg-newapi/codestral-embed-2505
```
