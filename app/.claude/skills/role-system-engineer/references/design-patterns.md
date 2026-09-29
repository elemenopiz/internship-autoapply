# Design Patterns Quick Reference

## Creational Patterns
| Pattern | Use When | Example |
|---------|----------|---------|
| Factory Method | Object creation logic centralized | `createLogger("file")` → FileLogger |
| Builder | Complex object step-by-step | `QueryBuilder().select("*").from("users").where(...)` |
| Singleton | Exactly one instance (use sparingly) | Database connection pool, config |
| Prototype | Clone existing objects is cheaper | Deep-copy template objects |

## Structural Patterns
| Pattern | Use When | Example |
|---------|----------|---------|
| Adapter | Incompatible interfaces | Wrap third-party API to match internal interface |
| Decorator | Add behavior dynamically | `LoggingDecorator(AuthDecorator(handler))` |
| Facade | Simplify complex subsystem | `PaymentFacade` wrapping gateway + fraud + ledger |
| Proxy | Control access (cache, lazy, auth) | `CachingProxy` for expensive API calls |
| Composite | Tree structures | File system: folders contain files and folders |

## Behavioral Patterns
| Pattern | Use When | Example |
|---------|----------|---------|
| Strategy | Multiple algorithms selectable at runtime | `SortStrategy`: quick, merge, heap |
| Observer/Pub-Sub | Objects react to changes | Event emitter: `order.on("created", notifyWarehouse)` |
| Command | Encapsulate actions (undo, queue) | `UndoableCommand` with execute() + undo() |
| State | Behavior changes with internal state | Order: PENDING → PAID → SHIPPED → DELIVERED |
| Chain of Responsibility | Process through handler series | Express/Koa middleware chain |
| Template Method | Algorithm skeleton with customizable steps | `BaseProcessor.process()` with abstract `validate()` |
| Iterator | Sequential access without exposing internals | Custom collection traversal |

## Architectural Patterns
| Pattern | Use When | Key Idea |
|---------|----------|----------|
| MVC / MVP / MVVM | Separate UI from logic | View ↔ Controller ↔ Model |
| Repository | Abstract data access | `UserRepository.findById(id)` hides DB details |
| Service Layer | Coordinate business ops | `OrderService.placeOrder()` orchestrates steps |
| Event Sourcing | Full audit trail | Store events, derive state: `[Created, Updated, Shipped]` |
| CQRS | Read/write models differ | Separate `CommandHandler` and `QueryHandler` |
| Hexagonal (Ports+Adapters) | Business logic independent of I/O | Core has ports; adapters plug in |
| Saga | Distributed transactions | Choreography or orchestration across services |
| Circuit Breaker | Protect against cascading failures | Open → Half-Open → Closed states |
| Strangler Fig | Incremental migration | Route traffic to new service gradually |
| Sidecar | Cross-cutting concerns | Service mesh proxy for logging, auth, tracing |

## Anti-Patterns to Recognize
| Anti-Pattern | Symptom | Fix |
|---|---|---|
| God Object | One class/module does everything | Split by single responsibility |
| Spaghetti Code | No clear structure | Extract functions, add layers |
| Golden Hammer | Same tool for every problem | Choose tool for the problem |
| Lava Flow | Dead code nobody removes | Delete it (tests catch issues) |
| Copy-Paste | Duplicated logic | Extract shared function |
| Premature Optimization | Complex perf tricks without data | Measure first |
| Distributed Monolith | Microservices tightly coupled | Bounded contexts, async communication |
| Anemic Domain Model | Entities with no behavior | Move logic into domain objects |
| Leaky Abstraction | Implementation details exposed | Strengthen interface boundary |
| Vendor Lock-in | Direct dependency on one provider | Abstract behind internal interface |
