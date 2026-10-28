# Problem 10

For any integers $n, k \ge 1$ and $s \ge 1$, let $F_m$ denote the $m$-th Fibonacci number. Define $\Phi_{n,k} = F_{nk} / F_k$, and recursively define $\Phi_{n,k}^{(1)} = \sum_{j=0}^{n-1} \Phi_{j+1,k} \Phi_{n-j,k}$ and $\Phi_{n,k}^{(s)} = \sum_{j=0}^{n-1} \Phi_{j+1,k} \Phi_{n-j,k}^{(s-1)}$ for $s \ge 2$. Then $\Phi_{n,k}^{(s)} = 5^{-s} (F_k)^{-2s-1} \sum_{j=0}^{s} (-1)^{(k-1)j} \binom{n+2s}{j} \binom{n+s-1-j}{n-1} F_{k(n+2s-2j)}$.

## Metadata

- Title: Convolved Numbers of $k$-sections of the Fibonacci Sequence: Properties, Consequences
- Source: 2603.08636
- Authors: Vitaly M. Khamitov; Dmitriy Dmitrishin; Alexander Stokolos; Daniel Gray
