function [d,n] = pair_dn(P, S, ti, a, b)
% PAIR_DN  Distance and NLOSv blocker count between node columns a and b at
% time index ti (used for arbitrary relay->RX hops). Returns NaN if absent.
xa=S.X(ti,a); ya=S.Y(ti,a); xb=S.X(ti,b); yb=S.Y(ti,b);
if isnan(xa)||isnan(xb), d=NaN; n=NaN; return; end
d = hypot(xb-xa, yb-ya);
present = find(~isnan(S.X(ti,:)));
cand = setdiff(present, [a b]);
n = count_blockers(xa,ya,xb,yb, S.X(ti,cand), S.Y(ti,cand), P.vehW);
end
