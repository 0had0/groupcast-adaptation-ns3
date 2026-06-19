function G = geom_precompute(P, S)
% GEOM_PRECOMPUTE  Per (time,node) distance and NLOSv blocker count to the
% leader, computed once from trace geometry (independent of channel params so
% calibration/relay sweeps can reuse it).
%   G.D  [Nt x Nn]  distance node->leader (NaN if node absent), leader col = 0
%   G.Nb [Nt x Nn]  #blockers between node and leader
Nt = numel(S.t); Nn = numel(S.id); lc = S.leadCol;
D = nan(Nt,Nn); Nb = nan(Nt,Nn);
for ti = 1:Nt
    xl = S.X(ti,lc); yl = S.Y(ti,lc);
    if isnan(xl), continue; end
    present = find(~isnan(S.X(ti,:)));
    for j = present
        if j == lc, D(ti,j)=0; Nb(ti,j)=0; continue; end
        xj = S.X(ti,j); yj = S.Y(ti,j);
        D(ti,j) = hypot(xj-xl, yj-yl);
        cand = setdiff(present, [lc j]);
        Nb(ti,j) = count_blockers(xl,yl,xj,yj, S.X(ti,cand), S.Y(ti,cand), P.vehW);
    end
end
G.D = D; G.Nb = Nb;
end
