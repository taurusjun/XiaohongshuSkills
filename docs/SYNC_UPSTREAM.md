# 同步原仓库更新

本项目 Fork 自 [white0dew/XiaohongshuSkills](https://github.com/white0dew/XiaohongshuSkills)，本页说明如何同步原仓库的最新更新。

## 远程仓库配置

```bash
# 查看当前配置
git remote -v

# 应显示：
# origin    https://github.com/taurusjun/XiaohongshuSkills.git
# upstream  https://github.com/white0dew/XiaohongshuSkills.git
```

## 同步更新步骤

### 方法一：Merge（推荐）

```bash
# 1. 拉取原仓库最新代码
git fetch upstream

# 2. 切换到 main 分支
git checkout main

# 3. 合并原仓库更新
git merge upstream/main

# 4. 推送到你的 fork
git push origin main
```

### 方法二：Rebase（保持提交历史线性）

```bash
git fetch upstream
git checkout main
git rebase upstream/main
git push origin main --force
```

## 处理冲突

如果合并时出现冲突：

```bash
# 查看冲突文件
git status

# 手动编辑冲突文件，解决后
git add <冲突文件>
git commit -m "resolve merge conflicts"
git push origin main
```

## 日常开发建议

- 你的自定义脚本放在 `scripts/` 目录，避免修改原仓库的核心文件
- 同步前先提交本地改动：`git add . && git commit -m "xxx"`
- 定期同步，避免差异过大导致冲突难以解决
