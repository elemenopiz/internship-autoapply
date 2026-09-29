# MISRA-C:2012 Critical Rules Quick Reference

## Types and Conversions
| Rule | Level | Summary |
|------|-------|---------|
| 10.1 | Required | Operands shall not be of inappropriate essential type |
| 10.3 | Required | No implicit narrowing conversions |
| 10.4 | Required | Both operands same essential type category |

## Pointers
| Rule | Level | Summary |
|------|-------|---------|
| 11.3 | Required | No cast between pointer-to-object and integer (deviate for registers) |
| 11.8 | Required | Cast shall not remove const/volatile |
| 18.6 | Required | Pointer not used after object lifetime ends |

## Control Flow
| Rule | Level | Summary |
|------|-------|---------|
| 14.3 | Required | Controlling expression shall be boolean |
| 15.7 | Required | All if...else if chains have final else |
| 16.4 | Required | Every switch has default label |

## Functions
| Rule | Level | Summary |
|------|-------|---------|
| 17.2 | Required | No recursion |
| 17.7 | Required | Return value of non-void function shall be used |

## Memory & I/O
| Rule | Level | Summary |
|------|-------|---------|
| 21.3 | Required | No malloc/calloc/free |
| 21.6 | Required | No stdio in production code |

## Preprocessor
| Rule | Level | Summary |
|------|-------|---------|
| 20.7 | Required | Macro parameters shall be parenthesized |

## Deviation Format
```c
/* MISRA Deviation: Rule {N}
 * Justification: {reason}
 * Risk: {what risk}
 * Mitigation: {how controlled}
 */
```

## Static Analysis Tools
| Tool | License | MISRA Support |
|------|---------|---------------|
| PC-lint / FlexeLint | Commercial | Full |
| Polyspace | Commercial | Full + formal proof |
| LDRA | Commercial | Full, certification |
| cppcheck | Open source | Partial (addon) |
| clang-tidy | Open source | Partial |
