# Reference fixtures

`tests/refs/refs_A80.json` and `tests/refs/refs_A0.json` hold the MATLAB reference solutions of
the four EG2100 assignments: every numeric variable of `S1.m`, `S2.m`, `S3.m`, `LF.m` and the LF
variant with the bus-5 shunt rated at nominal voltage (`LF_q_rated`), with 17 significant digits.
`tests/refdata.py` loads them (`ref(80, "S2", "Zth7_pu")`); variable names are the MATLAB ones.

## Regenerating

The MATLAB scripts live in the coursework folder, outside this repository. From MATLAB:

```matlab
addpath('<PowerLab>/tools/refs')
export_refs('<coursework folder>', '<PowerLab>/tests/refs')
```

`export_refs` copies each assignment's `matlab/` folder to a temporary directory, replaces the
`A = ...;` line (80 and 0 by default) and runs the scripts in a disposable workspace, so the
originals are never modified. The LF scripts need the Optimization Toolbox (`fsolve`).

## Notes on the published A = 0 values

The assignment statement prints check values for A = 0. Those for S1 (`Z_I`), S2 (`U_Th7`,
`Z_Th7`) and LF (`P_loss = 26.4240 MW`) match `refs_A0.json`. The S3b values printed there contain
two apparent misprints:

| Quantity | Printed | MATLAB, A = 0 | Why the printed value is wrong |
|---|---|---|---|
| `Z_Th7,1 = Z_Th7,2` | 0.0694 + 0.0356i | 0.0649 + 0.0356i | S2b prints 0.0649 for the same quantity |
| `Z_Th7,0` | 0.1617 − 0.0656i | 0.1617 + 0.0656i | the zero-sequence network seen from bus 7 is inductive |
