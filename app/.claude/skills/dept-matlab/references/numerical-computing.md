# MATLAB Numerical Computing Reference

## Floating-Point Rules
```matlab
% NEVER: if (a == b)
% ALWAYS: if abs(a - b) < tol        % or eps(max(abs(a),abs(b)))
% TESTING: testCase.verifyEqual(a, b, 'AbsTol', 1e-10);
```

## Matrix Operations
```matlab
% NEVER: x = inv(A) * b    % Unstable, slow
% ALWAYS: x = A \ b         % Auto-selects LU/QR/Cholesky
% CHECK:  if cond(A) > 1e12, warning('Ill-conditioned'); end
```

## Pre-Allocation
```matlab
% NEVER: result = []; for i=1:n, result = [result; compute(i)]; end
% ALWAYS: result = zeros(n, m); for i=1:n, result(i,:) = compute(i); end
```

## Filter Design
```matlab
d = designfilt('lowpassfir', 'PassbandFrequency', 50, ...
    'StopbandFrequency', 100, 'SampleRate', 1000);
y = filtfilt(d, x);  % Zero-phase (offline)
y = filter(d, x);    % Causal (real-time)
```

## FFT Pattern
```matlab
N = length(x); w = hann(N); X = fft(x .* w);
X_ss = X(1:floor(N/2)+1);
X_ss(2:end-1) = 2 * X_ss(2:end-1);
f = (0:floor(N/2))' * fs / N;
mag_dB = 20 * log10(abs(X_ss)/N + eps);
```

## Control Systems
```matlab
sys = tf(num, den);
[Gm, Pm] = margin(sys);
[C, info] = pidtune(G, 'PID');
T = feedback(C*G, 1);
```

## Fixed-Point
```
fixdt(Signed, WordLength, FractionLength)
fixdt(1, 16, 8) → signed 16-bit, 8 fractional
  Range: [-128, +127.996], Resolution: 0.0039
```

## Common Errors
| Error | Cause | Fix |
|-------|-------|-----|
| Matrix dimensions must agree | Size mismatch | Check `size()` |
| Singular matrix | `inv()` on singular | Use `pinv()`, check `rank()` |
| Out of memory | Array too large | Use `sparse`, `tall`, or chunk |
