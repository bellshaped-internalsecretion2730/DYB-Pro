# Upstream pull-request archive

DYB Pro preserves the original ForesterKanez Git history while using these remotes:

- `origin`: `https://github.com/yacine-baghli/DYB-Pro.git`
- `upstream`: `https://github.com/DAKSHPATELL/ForesterKanez.git`

GitHub pull-request objects cannot be transferred between repositories. Their comments, reviews,
checks, timestamps, and original numbers therefore remain available on the upstream repository.
Their Git commits are preserved here using the following archive branches:

- Original PR `N`: `archive/upstream-pr-N` (PRs 1 through 30)
- Standalone experimental branch `bati-N`: `archive/upstream-bati-N` (bati-1 through bati-6)

Merged PR commits are also ancestors of `main`. The archive branches preserve the exact PR heads and
unmerged alternatives so they can be inspected, compared, or selectively cherry-picked later.

`bati-5` was an upstream branch rather than a GitHub pull request. It is preserved as
`archive/upstream-bati-5`.

## Recreated review pull requests

The historical variants explicitly selected for review are visible as draft pull requests in
DYB-Pro:

- [PR #1 — recovered bati-1 molecular research workbench](https://github.com/yacine-baghli/DYB-Pro/pull/1)
- [PR #2 — recovered bati-3 lean protein workspace](https://github.com/yacine-baghli/DYB-Pro/pull/2)
- [PR #3 — recovered bati-5 selection-aware protein agent](https://github.com/yacine-baghli/DYB-Pro/pull/3)

PR #1 uses `pr/recovered-upstream-bati-1` because the original branch had no common ancestor with
current `main`. Its bridge commit preserves the original tip as a second parent and reproduces its
tree exactly. PRs #2 and #3 use the exact archived upstream heads directly.

Original pull requests remain browsable at:
`https://github.com/DAKSHPATELL/ForesterKanez/pulls?q=is%3Apr`
