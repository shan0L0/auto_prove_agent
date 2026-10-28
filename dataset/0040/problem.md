# Problem 40

Let $n, a, b$ be positive integers with $a \mid b$. Let $S = \{x_1, \dots, x_n\}$ be a set of $n$ distinct positive integers. For $x \in S$, define $G_S(x) = \{d \in S \mid d < x, d \mid x \text{ and } \forall y \in S, (d \mid y \text{ and } y \mid x) \implies y \in \{d, x\}\}$. Let $(S^a)$ (resp. $[S^a]$) be the $n \times n$ integer matrix whose $(i,j)$-entry is $\gcd(x_i, x_j)^a$ (resp. $\mathrm{lcm}(x_i, x_j)^a$). Suppose $S$ is gcd-closed (i.e., for all $i,j$, $\gcd(x_i, x_j) \in S$) and satisfies condition $\mathcal{G}$: for any $x \in S$, either $|G_S(x)| \le 1$, or $|G_S(x)| \ge 2$ and for any distinct $y_1, y_2 \in G_S(x)$, $\mathrm{lcm}(y_1, y_2) = x$ and $\gcd(y_1, y_2) \in G_S(y_1) \cap G_S(y_2)$. Then there exist $n \times n$ integer matrices $C$ and $D$ such that $(S^b) = (S^a) C$ and $[S^b] = (S^a) D$.

## Metadata

- Title: Proof of Hong's conjecture on divisibility among power GCD and power LCM matrices on gcd-closed sets
- Source: 2603.26350
- Authors: Guangyan Zhu
