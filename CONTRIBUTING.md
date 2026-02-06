# Contributing to AxonRelay

## Setup

Enable local Git hooks to prevent accidental force pushes:

```bash
git config core.hooksPath .githooks
```

## Git Workflow Rules

### Branch Protection (Manual Enforcement)

Since branch protection is not available on GitHub Free for private repositories, we enforce these rules manually:

#### main branch

- **No direct commits** - All changes must go through Pull Requests
- **No force push** - Never use `git push --force` on main
- **No branch deletion** - main branch must never be deleted
- **Squash merge only** - Use "Squash and merge" for all PRs

#### Feature branches

- Naming convention: `feature/<description>` or `fix/<description>`
- Delete after merge

### Pull Request Workflow

1. **Create feature branch**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/your-feature
   ```

2. **Make changes and commit**
   ```bash
   git add <specific-files>
   git commit -m "Description of changes"
   ```

3. **Push and create PR**
   ```bash
   git push -u origin feature/your-feature
   gh pr create --title "Title" --body "Description"
   ```

4. **Review and merge**
   - Self-review the diff before merging
   - Use "Squash and merge" button
   - Delete branch after merge

### Commit Message Guidelines

- Use present tense ("Add feature" not "Added feature")
- Keep first line under 72 characters
- Include `Co-Authored-By` for AI-assisted commits

### Dangerous Commands Checklist

Before running these commands, **stop and think**:

| Command | Risk | Alternative |
|---------|------|-------------|
| `git push --force` | Overwrites remote history | `git push` (no force) |
| `git reset --hard` | Loses uncommitted changes | `git stash` first |
| `git checkout .` | Discards all changes | `git stash` |
| `git branch -D` | Deletes branch permanently | `git branch -d` (safe) |
| `git clean -f` | Deletes untracked files | Review with `git clean -n` first |

### Recovery

If you accidentally push to main:

```bash
# 1. Don't panic
# 2. Create a revert commit (don't force push)
git revert HEAD
git push origin main

# 3. Notify team members
```
