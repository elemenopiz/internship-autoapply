# Data Science Statistical Methods Reference

## Hypothesis Testing Decision Tree
```
Is data normal? → Shapiro-Wilk test (n < 50) or K-S test (n ≥ 50)
  ├── YES: Parametric tests
  │     ├── 2 groups, independent → Independent t-test
  │     ├── 2 groups, paired → Paired t-test
  │     ├── 3+ groups → One-way ANOVA → post-hoc Tukey
  │     └── 2+ factors → Two-way ANOVA
  └── NO: Non-parametric tests
        ├── 2 groups, independent → Mann-Whitney U
        ├── 2 groups, paired → Wilcoxon signed-rank
        ├── 3+ groups, independent → Kruskal-Wallis
        └── 3+ groups, paired → Friedman test

Categorical data:
  ├── 2×2 table → Chi-squared or Fisher's exact (small n)
  ├── r×c table → Chi-squared
  └── Proportions → Z-test for proportions
```

## Multiple Comparison Corrections
| Method | When | How |
|--------|------|-----|
| Bonferroni | Few comparisons, conservative | α_adjusted = α / n_tests |
| Holm-Bonferroni | Moderate comparisons | Step-down procedure |
| Benjamini-Hochberg | Many comparisons (FDR) | Controls false discovery rate |

## Effect Size Measures
| Test | Effect Size | Small | Medium | Large |
|------|------------|-------|--------|-------|
| t-test | Cohen's d | 0.2 | 0.5 | 0.8 |
| ANOVA | Eta-squared (η²) | 0.01 | 0.06 | 0.14 |
| Chi-squared | Cramér's V | 0.1 | 0.3 | 0.5 |
| Correlation | Pearson's r | 0.1 | 0.3 | 0.5 |

Always report effect size alongside p-value. Statistical significance ≠ practical significance.

## A/B Test Sample Size Calculator
```python
from scipy import stats
import numpy as np

def required_sample_size(baseline_rate, mde, alpha=0.05, power=0.80):
    """Calculate required sample size per group for proportion test."""
    p1 = baseline_rate
    p2 = baseline_rate * (1 + mde)  # e.g., 5% improvement
    effect_size = abs(p2 - p1) / np.sqrt(p1 * (1-p1))
    z_alpha = stats.norm.ppf(1 - alpha/2)
    z_beta = stats.norm.ppf(power)
    n = ((z_alpha + z_beta) / effect_size) ** 2
    return int(np.ceil(n))
```

## Model Selection Guide
| Problem | Algorithm | When |
|---------|-----------|------|
| Binary classification | Logistic Regression | Interpretable, linear boundary |
| Binary classification | Random Forest | Non-linear, feature importance |
| Binary classification | XGBoost/LightGBM | High performance, tabular data |
| Multi-class | Softmax / Multi-class LR | Few classes, interpretable |
| Regression | Linear Regression | Interpretable, linear relationship |
| Regression | XGBoost/LightGBM | Non-linear, tabular |
| Clustering | K-Means | Spherical clusters, known k |
| Clustering | DBSCAN | Arbitrary shape, outlier detection |
| Dimensionality reduction | PCA | Linear, variance preservation |
| Dimensionality reduction | t-SNE / UMAP | Visualization, non-linear |
| Time series | ARIMA / Prophet | Univariate forecasting |
| Text classification | TF-IDF + LR / BERT | NLP tasks |
| Image | CNN (ResNet, EfficientNet) | Computer vision |
| Tabular (deep) | TabNet / FT-Transformer | When tree methods plateau |

## Evaluation Metrics Cheat Sheet
| Task | Metric | When |
|------|--------|------|
| Classification (balanced) | Accuracy | Classes ~equal |
| Classification (imbalanced) | F1, Precision-Recall AUC | Rare positive class |
| Classification (ranking) | ROC-AUC | Threshold-independent |
| Regression | RMSE | Penalize large errors |
| Regression | MAE | Robust to outliers |
| Regression | R² | Proportion of variance explained |
| Clustering | Silhouette Score | No ground truth |
| Clustering | Adjusted Rand Index | With ground truth |

## Cross-Validation Strategies
| Strategy | When | How |
|----------|------|-----|
| K-Fold (k=5 or 10) | Standard tabular | Shuffle, split into k folds |
| Stratified K-Fold | Imbalanced classes | Preserve class distribution |
| Time-Series Split | Temporal data | Train on past, test on future |
| Leave-One-Out | Very small datasets | n folds of size n-1 |
| Group K-Fold | Grouped data (users) | No group leaks across folds |
