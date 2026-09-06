# Contributing to AxonRelay

## Setup

Enable the local Git hooks:

```bash
git config core.hooksPath .githooks
```

`.githooks/pre-push` then runs three guards on every push:

1. **Force push to `main`** - refused.
2. **Credentials** - the commits being pushed are scanned for API keys, tokens,
   private keys and passwords embedded in URLs. These patterns describe
   credential *shapes*, not values, so they are committed here and work with no
   setup.
3. **Personal information** - the same commits are scanned against a denylist
   you keep locally. Home directories, private addresses, an employer's name:
   such a list has to spell out the strings it protects, so committing it would
   publish exactly what it guards.

Only added lines and commit metadata are scanned. A commit that *removes* a
leaked string necessarily contains that string in its diff, and blocking the
fix is how a guard gets switched off for good.

The personal-information guard is skipped entirely when no denylist exists, so
it is optional - but it is the half that knows about you. To set one up:

```bash
mkdir -p ~/.config/axonrelay
$EDITOR ~/.config/axonrelay/push-denylist.txt
chmod 600 ~/.config/axonrelay/push-denylist.txt
```

One extended regular expression per line; a line starting with `!` is an
exception, which is what makes a broad pattern usable ("any email address"
minus the few that are meant to be public). One file there covers every clone
on the machine. `.githooks/denylist.local` (gitignored) works as a per-clone
override, and `$PUSH_DENYLIST_FILE` overrides both.

To push past either content guard once:

```bash
PUSH_GUARD_ALLOW=1 git push ...
```

It is recorded on stderr. A credential that ever reached a commit must be
rotated - removing it from the tree is not enough.

## Git Workflow Rules

### Branch Protection (Enforced by GitHub)

The repository is public, so branch protection is available and **enabled** on
`main`. These rules are enforced server-side, not by convention:

| Rule | Effect |
|---|---|
| Require a pull request | No direct pushes to `main`. Zero approvals are required (this is a solo project), but the PR is what runs CI |
| Require status checks | `Lint Backend (Python)`, `Test Backend (pytest)`, `Test Migrations (Postgres)`, `Build Frontend (pnpm)` must pass, and the branch must be up to date with `main` first |
| Require linear history | No merge commits - use "Squash and merge" |
| Require conversation resolution | Review threads must be resolved before merging |
| Block force pushes | `git push --force` to `main` is rejected |
| Block deletion | `main` cannot be deleted |
| Include administrators | The rules apply to the repository owner too |

Because administrators are included, there is no per-push escape hatch. A
history rewrite or any other operation that genuinely needs a force push
requires turning protection off in Settings, doing the work, and turning it
back on - deliberately, not in passing.

The `.githooks/pre-push` hook still exists as a local first line of defence,
and `git config core.hooksPath .githooks` is still worth running.

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
