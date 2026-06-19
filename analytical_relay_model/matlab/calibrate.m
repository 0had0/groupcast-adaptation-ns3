function [P, CAL] = calibrate(P, traces)
% CALIBRATE  Fit the reduced-form link model prr_cal to the ns-3 PRR(d,MCS)
% curves over the actual trace geometry. Shared geometric loss
% g(d,n)=PLexp*log10(d)+Cblock*n (2 params, 2-D outer search); per-MCS margin
% thr_k and width s_k fitted in a robust inner 2-D search against the pooled
% (both-trace) binned simulation PRR. Stores P.PLexp,P.Cblock,P.thr,P.sk.

T = numel(traces); K = numel(P.mcs.idx);
% per-trace samples + sim targets + bin assignment
for i = 1:T
    d = traces{i}.G.D(:); n = traces{i}.G.Nb(:);
    m = ~isnan(d) & d > 0;
    traces{i}.logd = log10(max(d(m),1)); traces{i}.nv = n(m);
    R = readmatrix(traces{i}.calib);
    centers = unique(R(:,2)); simP = nan(numel(centers),K);
    for r = 1:size(R,1)
        ci = find(centers==R(r,2),1); ki = find(P.mcs.idx==R(r,1),1);
        if ~isempty(ki), simP(ci,ki) = R(r,3); end
    end
    traces{i}.centers = centers; traces{i}.simP = simP;
    [~, traces{i}.binIdx] = min(abs((d(m)) - centers.'), [], 2);
end

% inner: fit (thr,s) for one MCS to pooled (gbar, sv), clamped & robust start
    function [thr,s,res] = fit_mcs(gbar, sv)
        ok = ~isnan(gbar) & ~isnan(sv); g = gbar(ok); y = sv(ok);
        if numel(g) < 2, thr = mean(g); if isnan(thr), thr=40; end; s = 4; res = 0; return; end
        clampS = @(v) min(max(v,0.5),15);
        % start: g nearest PRR=0.5; if no crossing (all y>0.5) start above max g
        [~,j] = min(abs(y - 0.5)); c0 = g(j);
        if min(y) > 0.5, c0 = max(g) + 4; end
        f = @(p) sum((0.5*erfc((g - p(1))/(sqrt(2)*clampS(p(2)))) - y).^2);
        bestp = [c0 4]; bestv = f(bestp);
        for s0 = [2 4 8 12]
            p = fminsearch(f, [c0 s0], optimset('Display','off','MaxFunEvals',1500));
            if f(p) < bestv, bestv = f(p); bestp = p; end
        end
        thr = bestp(1); s = clampS(bestp(2)); res = bestv;
        if ~isfinite(thr), thr = median(g); end
    end

% gbar per bin for given (PLexp,Cblock), pooled across traces -> per MCS
    function [G_all, S_all] = pooled(PLexp,Cblock)
        G_all = cell(K,1); S_all = cell(K,1);
        for kk=1:K, G_all{kk}=[]; S_all{kk}=[]; end
        for ii=1:T
            g = PLexp*traces{ii}.logd + Cblock*traces{ii}.nv;
            nc = numel(traces{ii}.centers);
            gbar = accumarray(traces{ii}.binIdx, g, [nc 1], @mean, NaN);
            for kk=1:K
                G_all{kk} = [G_all{kk}; gbar];
                S_all{kk} = [S_all{kk}; traces{ii}.simP(:,kk)];
            end
        end
    end

    function e = outer(PLexp,Cblock)
        [G_all,S_all] = pooled(PLexp,Cblock); e = 0;
        for kk=1:K, [~,~,r] = fit_mcs(G_all{kk},S_all{kk}); e = e + r; end
    end

% robust 2-D grid over (PLexp, Cblock)
PLg = 8:2:44; Cg = 0:0.5:10;
best = inf; bestPL = 20; bestC = 0;
for PLexp = PLg
    for Cblock = Cg
        e = outer(PLexp,Cblock);
        if e < best, best = e; bestPL = PLexp; bestC = Cblock; end
    end
end
P.PLexp = bestPL; P.Cblock = bestC;
P.Lb0_dB = P.Cblock; P.LbStep_dB = P.Cblock;   % keep names consistent
[G_all,S_all] = pooled(P.PLexp,P.Cblock);
P.thr = zeros(1,K); P.sk = zeros(1,K);
for kk=1:K, [P.thr(kk),P.sk(kk)] = fit_mcs(G_all{kk},S_all{kk}); end
 npts = sum(cellfun(@(c) sum(~isnan(c)), S_all));
P.calib_rmse = sqrt(best/npts);
P.bler_sigma = mean(P.sk);

% comparison curves for plotting (model vs sim per trace)
CAL = struct('name',{},'centers',{},'simP',{},'modP',{},'mcs',{});
for i=1:T
    nc = numel(traces{i}.centers); modP = nan(nc,K);
    g = P.PLexp*traces{i}.logd + P.Cblock*traces{i}.nv;
    gbar = accumarray(traces{i}.binIdx, g, [nc 1], @mean, NaN);
    for kk=1:K, modP(:,kk) = 0.5*erfc((gbar - P.thr(kk))/(sqrt(2)*P.sk(kk))); end
    CAL(i).name=traces{i}.name; CAL(i).centers=traces{i}.centers;
    CAL(i).simP=traces{i}.simP; CAL(i).modP=modP; CAL(i).mcs=P.mcs.idx;
end
end
