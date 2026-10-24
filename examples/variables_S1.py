"""Example variable script for PowerLab.

Link this file from the Variables panel (Link script...) and every public
numeric global below becomes available to the expression fields, so you can
write the assignment's own algebra straight into the properties, e.g.

    length  ->  (217 + A)/1000
    P       ->  (117 - A)/1000

Change A here, press Reload, and every bound field follows.
"""

# Your A-number.
A = 80

# Cable data shared by both feeders (EG2100 Assignment S1).
r_cable = 0.16          # ohm/km
x_cable = 0.08          # ohm/km
bc_cable = 3.0e-6       # S/km

# Handy derived values.
len_LI_km = (217 + A) / 1000
P_LDb_MW = (117 - A) / 1000
P_LDe_MW = (243 + A) / 1000
