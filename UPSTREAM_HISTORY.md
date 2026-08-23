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

Original pull requests remain browsable at:
`https://github.com/DAKSHPATELL/ForesterKanez/pulls?q=is%3Apr`
