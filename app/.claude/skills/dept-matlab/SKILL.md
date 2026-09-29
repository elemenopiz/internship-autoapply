---
name: dept-matlab
description: >
  Specialist Department: MATLAB & Simulation. Activated when project involves .m/.mlx/.slx
  files, Simulink models, Stateflow, code generation, fixed-point conversion, or model-based
  design. Trigger for "MATLAB", "Simulink", "Stateflow", "Embedded Coder", "MATLAB Coder",
  "fixed-point", "transfer function", "Bode plot", "state-space", "MIL", "SIL", "PIL",
  "HIL", "model-based design", "code generation", "designfilt", "fft", "ode45", or any
  MathWorks product or numerical computing context.
---

# Specialist Department: MATLAB & Simulation

## Activation
Activates when ANY of: .m, .mlx, .slx, .mdl files detected; MATLAB path setup
(startup.m, pathdef.m); toolbox usage; Simulink blocks; Stateflow charts; Embedded
Coder configuration; or user explicitly states MATLAB context.

---

## Phase Injections Into All Roles

### → Requirements Analyst
Additional NFR categories to capture:
- **Numerical accuracy**: Tolerance bands for each output signal. Reference comparison method
  (analytical solution, validated tool, high-fidelity simulation).
- **Fixed-point requirements**: Word length constraints, overflow strategy (saturate/wrap),
  precision requirements per signal.
- **Code generation targets**: Target MCU, execution time budget per step, memory budget,
  MISRA compliance for generated code.
- **Simulation requirements**: Solver type (fixed/variable step), step size, simulation
  duration, real-time factor target.
- **Toolbox dependencies**: Which MATLAB toolboxes are required (Signal Processing, Control
  System, Image Processing, etc.).

### → System Engineer
Additional design concerns:
- **MATLAB package namespace**: Organize code in `+pkgname/` packages to avoid name collisions.
- **Simulink model hierarchy**: Top model → subsystems. Bus naming conventions.
- **Signal naming with units**: `velocity_mps`, `temperature_degC`, `pressure_kPa`, `angle_rad`.
  Every signal name includes unit suffix.
- **Parameter naming**: `maxTorque_Nm`, `filterOrder`, `sampleRate_Hz`. Include unit.
- **Fixed-point scaling strategy**: Document scaling for each signal path. Use Fixed-Point
  Designer to analyze ranges and propose types.
- **MIL/SIL/PIL/HIL test levels**:
  - MIL: Simulink model tested in Simulink.
  - SIL: Generated C code tested on host.
  - PIL: Generated code on target MCU.
  - HIL: Generated code on target with simulated plant.

### → Backend Developer (MATLAB Developer)
MATLAB coding standards:
- **Function headers**: H1 line + full help block. Use `arguments` block (R2019b+) for type
  and range validation on all public functions.
  ```matlab
  function [output] = calculateTrajectory(pos, vel, dt, options)
  %CALCULATETRAJECTORY Compute projectile trajectory with drag.
  %   [POS] = CALCULATETRAJECTORY(P0, V0, DT) computes trajectory...
      arguments
          pos (3,1) double
          vel (3,1) double
          dt (1,1) double {mustBePositive}
          options.dragCoeff (1,1) double {mustBeNonnegative} = 0.47
      end
  ```
- **Vectorize**: Prefer vectorized operations over loops. Use `arrayfun`, `bsxfun`.
  Document when loops are intentional (memory, algorithm requirement).
- **Pre-allocate**: `result = zeros(n, m)` before loops. NEVER grow arrays in loops.
- **No eval/evalin/feval(string)**: Security risk and prevents static analysis.
- **No global variables**: Pass data through arguments or object properties.
- **eps for float comparison**: NEVER `if (a == b)`. ALWAYS `if abs(a-b) < tol`.
- **Use `\` not `inv()`**: `x = A \ b` is numerically stable. `x = inv(A) * b` is not.
  Check condition: `if cond(A) > 1e12, warning('Ill-conditioned'); end`
- **Naming**: camelCase functions, PascalCase classes, UPPER_SNAKE constants.
  snake_case for Simulink models, camelCase signals with unit suffix.
- **80 char line max**. Wrap with `...`. 4-space indent. One statement per line.
- **Section breaks**: `%% Section Name` to organize scripts.
- **Simulink**: Run Model Advisor before delivery. Pass MAAB / ISO 26262 / DO-331 checks
  as applicable. Complete signal naming (no unlabeled wires).

### → Unit Tester
MATLAB testing standards:
- Use `matlab.unittest.TestCase` with method-based tests.
- Traceability: `% Validates FR-001, AC-001-1` comment on every test.
- **Numerical assertions**: `testCase.verifyEqual(actual, expected, 'AbsTol', 1e-10)` or
  `'RelTol', 1e-6`. NEVER use exact equality for floating-point.
- **Performance**: `timeit(@() functionToTest(args))` to validate NFR timing budgets.
- **Coverage**: Use `CodeCoveragePlugin` for coverage reporting.
- **Test NaN/Inf**: Verify explicit handling (not silent propagation).
- **Test empty inputs**: `[]`, `{}`, empty strings.
- **Test ill-conditioned inputs**: Near-singular matrices, near-zero denominators.
- **Test dimensional correctness**: Input/output sizes match specification.
- **Simulink testing**: `sltest.TestCase` for model testing. Equivalence testing for
  MIL vs SIL, SIL vs PIL. Use test harness for each subsystem.

### → Integration Tester
- **MIL → SIL equivalence**: Same inputs to Simulink model and generated C code → outputs
  match within tolerance.
- **SIL → PIL equivalence**: Same inputs on host and target MCU → outputs match.
- **Model-level integration**: Test subsystem interactions within the top model.
- **Hardware integration**: If targeting real hardware, test sensor/actuator interfaces.

### → Security Engineer
- No `eval`, `evalc`, `evalin`, `feval(string)` — injection risk.
- No `system()` or `!` command with user-supplied strings.
- Validate all file paths before `fopen`, `load`, `save`.
- Use `matfile` or `datastore` for large data (not `load` into workspace).
- Handle file I/O errors with try/catch.
- For production generated code: Sign code artifacts, verify authenticity.

### → Documenter
- Document all functions with full MATLAB help block format.
- Simulink models: Document each subsystem purpose in block annotations.
- Include signal flow descriptions for complex models.
- Data dictionary: Document all workspace variables, bus definitions, enumerations.

### → Release Engineer
Additional release artifacts:
- **Code generation report** (if Embedded Coder used): HTML report from codegen.
- **Fixed-point comparison report**: Float vs fixed-point results comparison with error analysis.
- **MIL/SIL equivalence test report**: Showing matching results within tolerance.
- **Model Advisor results**: Showing all checks passed or documented exceptions.
- **Toolbox dependency list**: `matlab.codetools.requiredFilesAndProducts` output.
- **MATLAB version and toolbox versions**: For reproducibility.

---

## Common MATLAB Toolboxes

| Toolbox                      | Use Case                                    |
|------------------------------|---------------------------------------------|
| Signal Processing            | Filtering, FFT, spectral analysis           |
| Control System               | Transfer functions, Bode, root locus        |
| Image Processing             | Filtering, segmentation, feature extraction |
| Statistics & Machine Learning| Regression, classification, clustering      |
| Optimization                 | Linear/nonlinear solvers, genetic algorithms|
| Symbolic Math                | Analytical solutions, symbolic derivation   |
| Simulink                     | Dynamic system modeling and simulation      |
| Stateflow                    | State machines, sequential logic            |
| Embedded Coder               | Production C/C++ code generation            |
| MATLAB Coder                 | General-purpose C/C++ code generation       |
| Simulink Test                | Test harness management, equivalence testing|
| Fixed-Point Designer         | Fixed-point conversion and optimization     |
| DSP System                   | Streaming signal processing                 |
| Communications               | Modulation, coding, channel models          |
| Aerospace                    | Flight dynamics, coordinate transforms      |
| Robotics System              | Kinematics, path planning, ROS interface    |
| Automated Driving            | Perception, planning, vehicle dynamics      |
| Sensor Fusion & Tracking     | Kalman filters, multi-object tracking       |
| Deep Learning                | Neural networks, training, inference        |
| Computer Vision              | Feature detection, object recognition       |
| Radar                        | Waveform design, target detection           |

---

## Reference Files

- `references/numerical-computing.md` — Float pitfalls, FFT patterns, filter design, control
  systems, fixed-point notation, common error fixes.
