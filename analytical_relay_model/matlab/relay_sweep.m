function R = relay_sweep(P, S, PG, mcsList, xgrid)
% RELAY_SWEEP  Analytical single-relay placement sweep over boundary x.
% Strict-closest election; cooperative union (direct OR relay) within PDB.
% Relay link Broadcast (no feedback). Relay aids far node f only if the relay
% is ahead of f (closer to leader). Returns time-averaged metrics.
lc = S.leadCol; Nt = numel(S.t);
nK = numel(mcsList); nx = numel(xgrid);
kpos = arrayfun(@(m) find(P.mcs.idx==m,1), mcsList);

sumU = zeros(nx,nK); nObs = zeros(nx,nK);
minSum = zeros(nx,nK); minN = zeros(nx,nK);
sumB = zeros(1,nK); nObsB = 0;

for ti = 1:Nt
    if isnan(S.X(ti,lc)), continue; end          % leader absent this bin
    present = find(~isnan(S.X(ti,:)));
    F = present(present~=lc);
    if isempty(F), continue; end
    dL = squeeze(PG.D(lc,:,ti)); nL = squeeze(PG.N(lc,:,ti));
    prD = zeros(numel(F), nK);
    for kk = 1:nK, prD(:,kk) = prr_cal(P, dL(F), nL(F), kpos(kk)).'; end
    sumB = sumB + sum(prD,1); nObsB = nObsB + numel(F);
    dLF = dL(F).';
    for xi = 1:nx
        [~,ri] = min(abs(dL(F) - xgrid(xi))); r = F(ri);
        drf = squeeze(PG.D(r,F,ti)); nrf = squeeze(PG.N(r,F,ti));
        ahead = (dL(r) < dLF) & (F.' ~= r);
        for kk = 1:nK
            prLr = prr_cal(P, dL(r), nL(r), kpos(kk));
            prRF = prr_cal(P, drf, nrf, kpos(kk)).';
            relayPath = prLr .* prRF .* ahead;
            u = 1 - (1 - prD(:,kk)) .* (1 - relayPath);
            sumU(xi,kk) = sumU(xi,kk) + sum(u); nObs(xi,kk) = nObs(xi,kk) + numel(F);
            minSum(xi,kk) = minSum(xi,kk) + min(u); minN(xi,kk) = minN(xi,kk) + 1;
        end
    end
end
R.x = xgrid(:); R.mcs = mcsList(:).';
R.gprr = sumU ./ nObs; R.base = sumB ./ nObsB;
R.minprr = minSum ./ minN;
[R.optgain, ix] = max(R.gprr - R.base, [], 1);
R.optx = xgrid(ix);
end
