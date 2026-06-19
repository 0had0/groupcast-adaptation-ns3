function PG = allpair_geom(P, S)
% ALLPAIR_GEOM  Distance and NLOSv blocker count between EVERY node pair at
% every sampled time, computed once. Enables fast relay-hop evaluation
% (relay->RX) without recomputing geometry per placement/MCS.
%   PG.D [Nn x Nn x Nt]  pairwise distance (NaN if either absent)
%   PG.N [Nn x Nn x Nt]  pairwise NLOSv blocker count
Nt = numel(S.t); Nn = numel(S.id);
D = nan(Nn,Nn,Nt); N = nan(Nn,Nn,Nt);
for ti = 1:Nt
    present = find(~isnan(S.X(ti,:)));
    x = S.X(ti,:); y = S.Y(ti,:);
    for ia = 1:numel(present)
        a = present(ia);
        for ib = ia+1:numel(present)
            b = present(ib);
            d = hypot(x(b)-x(a), y(b)-y(a));
            cand = present(present~=a & present~=b);
            nb = count_blockers(x(a),y(a),x(b),y(b), x(cand), y(cand), P.vehW);
            D(a,b,ti)=d; D(b,a,ti)=d; N(a,b,ti)=nb; N(b,a,ti)=nb;
        end
        D(a,a,ti)=0; N(a,a,ti)=0;
    end
end
PG.D = D; PG.N = N;
end
