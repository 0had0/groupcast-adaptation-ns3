function n = count_blockers(xA,yA,xB,yB, xs,ys, vehW)
% COUNT_BLOCKERS  Number of candidate vehicles geometrically obstructing the
% LoS segment A->B (NLOSv proxy for the ns-3 Liang-Barsky rectangle test):
% a vehicle blocks if its centre projects onto the interior of segment AB and
% lies within vehW/2 of the line (i.e. its body straddles the ray).
% xs,ys : candidate blocker positions (exclude A and B before calling).
ux = xB - xA; uy = yB - yA;
L2 = ux*ux + uy*uy;
if L2 < 1e-9, n = 0; return; end
valid = ~isnan(xs) & ~isnan(ys);
xs = xs(valid); ys = ys(valid);
% projection parameter s in (0,1)
s = ((xs - xA)*ux + (ys - yA)*uy) / L2;
inside = s > 0.02 & s < 0.98;
% perpendicular distance to the line
projx = xA + s*ux; projy = yA + s*uy;
perp = hypot(xs - projx, ys - projy);
n = sum(inside & (perp <= vehW/2));
end
