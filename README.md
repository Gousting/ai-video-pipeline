# ai-video-pipeline

多阶段 AI 视频流水线（脚本 → 分镜图 → 角色包 → 视频 → 音频 → 成片），用 opencode 多 agent 编排。

阶段（`pipeline.yaml`）：`script` → `image` → `character` → `video` → `audio` → `final`，每个阶段带验收 gate。

## 出片（video 阶段）的资产依赖

出片时「风格名 + 动作 ID」一起传：

- **风格名** → image-gen 的 style registry（锁画面质感）
- **动作 ID** → [Gousting/motion-library](https://github.com/Gousting/motion-library) 的 index.yaml 索引（锁运动形态）

即：视频阶段的动作模板来自 Gousting/motion-library（动作 ID 索引），参考生视频走 R2V
（MiniMaxH3ReferenceToVideo，ref2va）链路——参考图锁角色 + 参考视频锁动作。详见
`r2v_test_report.txt`。
