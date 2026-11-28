# ENSORA+ — Cours Synthétique de Démonstration : Économétrie Linéaire (MCO & Gauss-Markov)

> **Avertissement (Données synthétiques)** : Document pédagogique entièrement synthétique créé pour la démonstration hors-ligne d'Ensora+ (RAG, citations, AI Tutor et Learning Studio). Ne provient d'aucun support privé d'enseignant.

## Chapitre 1 — Modèle de régression linéaire multiple

On considère le modèle linéaire :
$$y = X\beta + \varepsilon$$
où $y \in \mathbb{R}^n$ est le vecteur des observations, $X \in \mathbb{R}^{n \times k}$ est la matrice des variables explicatives supposée de plein rang colonne ($\operatorname{rg}(X) = k$), $\beta \in \mathbb{R}^k$ est le vecteur inconnu des paramètres et $\varepsilon \in \mathbb{R}^n$ est le terme d'erreur.

### 1.1 Estimateur des Moindres Carrés Ordinaires (MCO / OLS)

L'estimateur MCO minimise la somme des carrés des résidus $S(\beta) = \|y - X\beta\|_2^2$. Les conditions du premier ordre donnent les équations normales $X^\top X \hat{\beta} = X^\top y$, d'où :
$$\hat{\beta}_{\text{MCO}} = (X^\top X)^{-1} X^\top y$$

```python
import numpy as np

def ols_estimate(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Compute the OLS estimator using a numerically stable least-squares solver."""
    beta_hat, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta_hat
```

## Chapitre 2 — Théorème de Gauss-Markov (Estimateur BLUE)

### 2.1 Hypothèses de Gauss-Markov
1. **Linéarité** du modèle : $y = X\beta + \varepsilon$.
2. **Exogénéité stricte** (espérance nulle des erreurs conditionnellement à $X$) : $\mathbb{E}[\varepsilon \mid X] = 0$.
3. **Homoscédasticité et absence d'autocorrélation** (erreurs sphériques) : $\operatorname{Var}(\varepsilon \mid X) = \sigma^2 I_n$ avec $\sigma^2 > 0$.
4. **Absence de colinéarité parfaite** : $\operatorname{rg}(X) = k$.

### 2.2 Énoncé du théorème
Sous les hypothèses de Gauss-Markov, l'estimateur $\hat{\beta}_{\text{MCO}}$ est **BLUE** (*Best Linear Unbiased Estimator*) : parmi tous les estimateurs linéaires en $y$ et sans biais de $\beta$, il possède la matrice de variance-covariance minimale au sens de l'ordre de Loewner :
$$\operatorname{Var}(\hat{\beta}_{\text{MCO}} \mid X) = \sigma^2 (X^\top X)^{-1}$$

### 2.3 Pièges classiques à l'examen
- La normalité des erreurs ($\varepsilon \mid X \sim \mathcal{N}(0, \sigma^2 I_n)$) n'est **pas** nécessaire pour le théorème de Gauss-Markov ; elle sert pour les lois exactes des tests $t$ de Student et $F$ de Fisher à taille finie.
- En présence d'hétéroscédasticité ($\operatorname{Var}(\varepsilon \mid X) = \Omega \neq \sigma^2 I_n$), $\hat{\beta}_{\text{MCO}}$ reste sans biais mais n'est plus de variance minimale (on utilise les MCG / GLS ou les écarts-types robustes de White).
