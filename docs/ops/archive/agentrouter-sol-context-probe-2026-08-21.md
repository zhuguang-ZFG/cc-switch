# agentrouter gpt-5.6-sol 上下文/输出上限实测（2026-08-21）

起因：OMP models.yml 里 agentrouter 的 `gpt-5.6-sol` 声明
262144/32768，而 models.dev 官方为 1050000/128000，zg-newapi/ooioo
声明 400000/128000——四个 provider 四个值，需实测 agentrouter 真实上限。

## 实测（直连 agentrouter `http://100.83.32.95:8788/v1`，chat/completions）

| 探针 | 结果 | 说明 |
|---|---|---|
| max_tokens=40000（声明上限 32768 之上） | 200 | 输出上限声明不实，API 不预校验 max_tokens |
| input 200,012 tokens | 200（21.5s） | 低于声明值，不证伪 |
| input 300,012 tokens | 200（47.8s） | **超过 262144 仍接受，声明证伪** |
| input 450,012 tokens | 200（94.0s） | 超过 OpenAI 官方 400k 档，渠道未在此截断 |

## 结论与处置

- agentrouter 的 sol 真实上下文 **>450k**（实测下界），声明 262144 属随手填，
  default 角色（`agentrouter/gpt-5.6-sol:max`）被白白砍掉 75% 上下文。
- models.yml 已改为 **1000000/128000**（models.dev 官方 1050000 留 5% 余量；
  实测背书到 450k，满刻度未探——1M 探针约 $2 + 3min，未执行）。
- max_tokens 128000 依 models.dev；渠道不预校验，无法零成本证伪，实际
  生成超限会自然截断，风险低。
- zg-newapi/ooioo 的 400000/128000（OpenAI 官方档）与 anyrouter-sol 的
  200000/128000 未实测，保持原值；如默认路由切到这些 provider 且需要
  >400k 上下文，再按本流程探。
