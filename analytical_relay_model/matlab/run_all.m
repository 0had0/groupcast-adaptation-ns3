function run_all()
% RUN_ALL  Analytical relay-selection/placement study over the platoon traces.
% Calibrates the link model to the ns-3 PRR(d) curves, evaluates pool/PRR/PIR/
% throughput/resource over both traces, and sweeps relay placement + variants.
% Writes all figures to ../figures and a numeric summary to ../data.

here = fileparts(mfilename('fullpath'));
root = fileparts(here);
figdir = fullfile(root,'figures'); if ~exist(figdir,'dir'), mkdir(figdir); end
datadir = fullfile(root,'data');
set(groot,'defaultFigureColor','w');
try, theme(groot,'light'); catch, end   % R2025b: force light theme for report figures

P = params();

% ---- load traces + all-pair geometry ----
fprintf('Loading traces + geometry...\n');
Sivd = load_trace(fullfile(datadir,'platoon_ivd_sweep.mob.csv'), 1.0);
Smrg = load_trace(fullfile(datadir,'platoon_merge_incremental.mob.csv'), 1.0);
PGi = allpair_geom(P, Sivd);
PGm = allpair_geom(P, Smrg);
Gi.D = squeeze(PGi.D(Sivd.leadCol,:,:)).'; Gi.Nb = squeeze(PGi.N(Sivd.leadCol,:,:)).';
Gm.D = squeeze(PGm.D(Smrg.leadCol,:,:)).'; Gm.Nb = squeeze(PGm.N(Smrg.leadCol,:,:)).';

% ---- calibrate link model to simulated PRR(d) ----
fprintf('Calibrating link model...\n');
traces = {struct('S',Sivd,'G',Gi,'calib',fullfile(datadir,'calib_ivd_prr_vs_dist.csv'),'name','IVD'), ...
          struct('S',Smrg,'G',Gm,'calib',fullfile(datadir,'calib_merge_prr_vs_dist.csv'),'name','Merge')};
[P, CAL] = calibrate(P, traces);
fprintf('  fitted reduced-form: PLexp=%.2f Cblock=%.2f  RMSE=%.4f\n', ...
        P.PLexp,P.Cblock,P.calib_rmse);
fid=fopen(fullfile(datadir,'calibrated_params.txt'),'w');
fprintf(fid,'%% Reduced-form link model: PRR_k = 0.5*erfc((g - thr_k)/(sqrt2*s_k))\n');
fprintf(fid,'%% with g(d,n) = PLexp*log10(d) + Cblock*n  (effective excess loss, dB)\n');
fprintf(fid,'PLexp=%.4f\nCblock_dB_per_blocker=%.4f\nRMSE=%.4f\n',P.PLexp,P.Cblock,P.calib_rmse);
fprintf(fid,'mcs   ='); fprintf(fid,'%6d ',P.mcs.idx); fprintf(fid,'\n');
fprintf(fid,'thr_k ='); fprintf(fid,'%6.2f ',P.thr); fprintf(fid,'\n');
fprintf(fid,'s_k   ='); fprintf(fid,'%6.2f ',P.sk); fprintf(fid,'\n'); fclose(fid);

% ---- FIGURES ----
fig_calibration(CAL, figdir);
fig_geometry(Sivd, Gi, 'IVD', figdir);
fig_geometry(Smrg, Gm, 'Merge', figdir);
fig_resource(P, Sivd, Gi, Smrg, Gm, figdir, datadir);
fig_metrics(P, Gi, Gm, figdir);

% ---- relay placement ----
mcsList = [12 16 20 24 28];
xg_i = 5:5:110; xg_m = 5:5:240;
fprintf('Relay sweep IVD...\n');   Ri = relay_sweep(P, Sivd, PGi, mcsList, xg_i);
fprintf('Relay sweep Merge...\n'); Rm = relay_sweep(P, Smrg, PGm, mcsList, xg_m);
fig_relay(Ri, 'IVD', figdir);
fig_relay(Rm, 'Merge', figdir);
fig_relay_variants(P, Sivd, PGi, 'IVD', figdir, 20);
fig_relay_variants(P, Smrg, PGm, 'Merge', figdir, 20);

% ---- numeric summary ----
write_summary(Ri, Rm, datadir);
fprintf('DONE. Figures in %s\n', figdir);
end

% =====================================================================
function fig_calibration(CAL, figdir)
f = figure('visible','off','Position',[0 0 1100 460]);
for i=1:numel(CAL)
    subplot(1,2,i); hold on;
    c = CAL(i); cm = parula(numel(c.mcs));
    for k=1:numel(c.mcs)
        plot(c.centers, c.simP(:,k), 'o', 'Color', cm(k,:), 'MarkerSize',4);
        plot(c.centers, c.modP(:,k), '-', 'Color', cm(k,:), 'LineWidth',1.4);
    end
    grid on; xlabel('distance from TX (m)'); ylabel('PRR'); ylim([0 1.02]);
    title(sprintf('%s — sim (o) vs model (—)', c.name));
    if i==1
        lg = arrayfun(@(m) sprintf('MCS %d',m), c.mcs, 'uni',0);
        legend(lg,'Location','southwest','FontSize',7);
    end
end
sgt('Link-model calibration to ns-3 PRR(d)');
save_fig(f, fullfile(figdir,'fig01_calibration.png'));
end

% =====================================================================
function fig_geometry(S, G, name, figdir)
f = figure('visible','off','Position',[0 0 1100 440]);
lc = S.leadCol; foll = setdiff(1:numel(S.id), lc);
cm = parula(numel(foll));
subplot(1,2,1); hold on;
for j=1:numel(foll)
    plot(S.t, G.D(:,foll(j)), '-', 'Color', cm(j,:));
end
grid on; xlabel('time (s)'); ylabel('distance to leader (m)');
title(sprintf('%s — range geometry', name));
subplot(1,2,2); hold on;
for j=1:numel(foll)
    plot(S.t, G.Nb(:,foll(j)), '-', 'Color', cm(j,:));
end
grid on; xlabel('time (s)'); ylabel('# NLOSv blockers to leader');
title(sprintf('%s — blockage geometry', name));
save_fig(f, fullfile(figdir, sprintf('fig02_geometry_%s.png', lower(name))));
end

% =====================================================================
function fig_resource(P, Si, Gi, Sm, Gm, figdir, datadir)
K = numel(P.mcs.idx);
bits = (P.payload_B + P.hdr_B)*8;
PRBraw = ceil(bits ./ (P.mcs.SE * P.RE_per_PRB));
subch = ceil(PRBraw / P.W);
slots = ceil(subch / P.Nsub);
% E[Ntx] from trace group BLER (use IVD geometry, first-tx worst-link)
ENtx = zeros(K,1);
dL = Gi.D; nB = Gi.Nb; m = ~isnan(dL) & dL>0;
for k=1:K
    fail = 1 - prr_cal(P, dL(m), nB(m), k);   % HARQ-baked failure proxy
    ENtx(k) = 1 + (P.Nmax-1)*mean(fail);
end
demand = P.lambda .* ENtx .* subch;          % subch-slots/s
CR = demand / P.Rpool;
% SB-SPS collision vs #TX
Ncsr = P.Nsub * round(P.selWin_slots * P.rho);
m_tx = 1:8; pcol = 1-(1-1/Ncsr).^(m_tx-1);

f = figure('visible','off','Position',[0 0 1200 720]);
subplot(2,2,1);
bar(P.mcs.idx, [subch slots]); grid on;
xlabel('MCS'); ylabel('per-TB resource'); legend({'subchannels','slots'},'Location','northeast');
title('Resource footprint of one TB (800 B)');
subplot(2,2,2);
bar(P.mcs.idx, CR*100); grid on; xlabel('MCS'); ylabel('Channel occupancy CR (%)');
title(sprintf('Single-flow CR (pool=%.0f subch-slots/s)', P.Rpool));
subplot(2,2,3);
plot(P.mcs.idx, ENtx,'-o'); grid on; xlabel('MCS'); ylabel('E[N_{tx}] per TB');
title('Expected transmissions (HARQ) vs MCS');
subplot(2,2,4);
plot(m_tx, pcol*100,'-o'); grid on; xlabel('# simultaneous TX (SB-SPS)');
ylabel('resource-collision prob (%)');
title(sprintf('SB-SPS collision (N_{CSR}=%d) vs deterministic (=0)', Ncsr));
sgt('Resource pool & multi-flow coexistence model');
save_fig(f, fullfile(figdir,'fig03_resource.png'));
% dump table
T = table(P.mcs.idx, PRBraw, subch, slots, ENtx, CR, ...
    'VariableNames',{'mcs','PRBraw','subch','slots','ENtx','CR'});
writetable(T, fullfile(datadir,'resource_table.csv'));
end

% =====================================================================
function fig_metrics(P, Gi, Gm, figdir)
% per-sample PRR over IVD geometry; PIR=RRI/PRR, throughput=lambda*B*PRR.
dL = Gi.D; nB = Gi.Nb; m = ~isnan(dL) & dL>0;
d = dL(m); n = nB(m);
edges = 0:5:max(d); ctr = edges(1:end-1)+2.5;
[~,~,bi] = histcounts(d, edges);
K = numel(P.mcs.idx); cm = parula(K);
f = figure('visible','off','Position',[0 0 1200 420]);
ax1=subplot(1,3,1); hold on; ax2=subplot(1,3,2); hold on; ax3=subplot(1,3,3); hold on;
for k=1:K
    pr = prr_cal(P, d, n, k);
    prb = accumarray(bi(bi>0), pr(bi>0), [numel(ctr) 1], @mean, NaN);
    pir = P.RRI ./ max(prb, 0.05) * 1000;          % ms (floor PRR at 5% = "dead")
    thr = P.lambda * P.payload_B*8/1000 .* prb;     % kbps
    plot(ax1, ctr, prb, '-', 'Color', cm(k,:));
    plot(ax2, ctr, pir, '-', 'Color', cm(k,:));
    plot(ax3, ctr, thr, '-', 'Color', cm(k,:));
end
grid(ax1,'on'); xlabel(ax1,'distance (m)'); ylabel(ax1,'PRR'); ylim(ax1,[0 1.02]); title(ax1,'PRR(d)');
grid(ax2,'on'); xlabel(ax2,'distance (m)'); ylabel(ax2,'PIR (ms)'); title(ax2,'PIR(d)=RRI/PRR');
ylim(ax2,[9 60]);
grid(ax3,'on'); xlabel(ax3,'distance (m)'); ylabel(ax3,'throughput (kbps)'); title(ax3,'goodput(d)');
lg = arrayfun(@(m) sprintf('MCS %d',m), P.mcs.idx,'uni',0);
legend(ax1, lg, 'Location','southwest','FontSize',7);
sgt('Analytical PRR / PIR / throughput vs distance (IVD geometry)');
save_fig(f, fullfile(figdir,'fig04_metrics.png'));
end

% =====================================================================
function fig_relay(R, name, figdir)
cm = parula(numel(R.mcs));
f = figure('visible','off','Position',[0 0 1200 460]);
subplot(1,2,1); hold on;
for k=1:numel(R.mcs)
    plot(R.x, R.gprr(:,k), '-', 'Color', cm(k,:), 'LineWidth',1.5);
    yline(R.base(k), ':', 'Color', cm(k,:));
end
grid on; xlabel('relay boundary x (m)'); ylabel('GPRR within PDB (union)');
ylim([0 1.02]); title(sprintf('%s — GPRR vs relay distance (dotted=no relay)', name));
lg = arrayfun(@(m) sprintf('MCS %d',m), R.mcs,'uni',0);
legend(lg,'Location','best','FontSize',7);
subplot(1,2,2); hold on;
for k=1:numel(R.mcs)
    plot(R.x, R.gprr(:,k)-R.base(k), '-', 'Color', cm(k,:), 'LineWidth',1.5);
    [~,ix]=max(R.gprr(:,k)-R.base(k)); plot(R.x(ix), R.gprr(ix,k)-R.base(k),'k*');
end
yline(0,'k'); grid on; xlabel('relay boundary x (m)'); ylabel('\Delta GPRR vs baseline');
title(sprintf('%s — relay gain (\\ast = optimal x)', name));
save_fig(f, fullfile(figdir, sprintf('fig05_relay_%s.png', lower(name))));
end

% =====================================================================
function fig_relay_variants(P, S, PG, name, figdir, leadMcs)
lc = S.leadCol; Nt = numel(S.t);
kL = find(P.mcs.idx==leadMcs,1);
% ---- (a) multi-relay chain: boundaries 10/20/50/100, cumulative ----
B = [10 20 50 100];
gprr_chain = zeros(1,numel(B)); base=0; nob=0;
% ---- (b) relay-MCS sweep at fixed single relay near best x (use 30 m IVD/40 merge heuristic) ----
xrel = 30; if strcmpi(name,'Merge'), xrel = 60; end
relMcsList = P.mcs.idx; gprr_relmcs = zeros(numel(relMcsList),1); nrm=0;
% ---- (c) selection variant: strict-closest vs best-link at x grid ----
xg = 5:5:max(80, round(max(PG.D(lc,:,1))));
gprr_sc = zeros(numel(xg),1); gprr_bl = zeros(numel(xg),1); nsel=0;

accChain = zeros(1,numel(B));
accRelMcs = zeros(numel(relMcsList),1);
accSC = zeros(numel(xg),1); accBL = zeros(numel(xg),1);

for ti=1:Nt
    if isnan(S.X(ti,lc)), continue; end
    present=find(~isnan(S.X(ti,:))); F=present(present~=lc);
    if isempty(F), continue; end
    dL=squeeze(PG.D(lc,:,ti)); nL=squeeze(PG.N(lc,:,ti));
    dLF=dL(F).';
    prD = prr_cal(P, dL(F), nL(F), kL).';   % |F|x1
    base = base + sum(prD); nob = nob + numel(F);

    % (a) chain cumulative
    relayPaths = zeros(numel(F), numel(B));
    for bi=1:numel(B)
        [~,ri]=min(abs(dL(F)-B(bi))); r=F(ri);
        prLr=prr_cal(P,dL(r),nL(r),kL);
        prRF=prr_cal(P,squeeze(PG.D(r,F,ti)),squeeze(PG.N(r,F,ti)),kL).';
        ahead=(dL(r)<dLF)&(F.'~=r);
        relayPaths(:,bi)=prLr.*prRF.*ahead;
    end
    for nb=1:numel(B)
        % union of direct + first nb relays
        pf = 1-prD;
        for bb=1:nb, pf = pf.*(1-relayPaths(:,bb)); end
        accChain(nb)=accChain(nb)+sum(1-pf);
    end

    % (b) relay-MCS sweep at fixed x=xrel
    [~,ri]=min(abs(dL(F)-xrel)); r=F(ri);
    ahead=(dL(r)<dLF)&(F.'~=r);
    drf=squeeze(PG.D(r,F,ti)); nrf=squeeze(PG.N(r,F,ti));
    prLr_all = arrayfun(@(kk) prr_cal(P,dL(r),nL(r),kk), 1:numel(relMcsList));
    for kk=1:numel(relMcsList)
        prRF=prr_cal(P,drf,nrf,kk).';
        rp = prLr_all(kk).*prRF.*ahead;
        u = 1-(1-prD).*(1-rp);
        accRelMcs(kk)=accRelMcs(kk)+sum(u);
    end
    nrm = nrm + numel(F);

    % (c) selection variants at x grid
    for xi=1:numel(xg)
        % strict-closest
        [~,ri]=min(abs(dL(F)-xg(xi))); r=F(ri);
        ahead=(dL(r)<dLF)&(F.'~=r);
        prLr=prr_cal(P,dL(r),nL(r),kL);
        prRF=prr_cal(P,squeeze(PG.D(r,F,ti)),squeeze(PG.N(r,F,ti)),kL).';
        u=1-(1-prD).*(1-prLr.*prRF.*ahead); accSC(xi)=accSC(xi)+sum(u);
        % best-link: among followers ahead of mid, pick max prLr*mean(prRF)
        candidates = F(dL(F) <= xg(xi) + 1e-9);   % relays at/ahead of boundary
        if isempty(candidates), cand = F; else, cand = candidates; end
        bestScore=-1; bestPath=zeros(numel(F),1);
        for cc=cand
            pl=prr_cal(P,dL(cc),nL(cc),kL);
            pr2=prr_cal(P,squeeze(PG.D(cc,F,ti)),squeeze(PG.N(cc,F,ti)),kL).';
            ah=(dL(cc)<dLF)&(F.'~=cc);
            sc = pl*mean(pr2.*ah);
            if sc>bestScore, bestScore=sc; bestPath=pl.*pr2.*ah; end
        end
        accBL(xi)=accBL(xi)+sum(1-(1-prD).*(1-bestPath));
    end
    nsel = nsel + numel(F);
end
gprr_chain = accChain / nob; base = base/nob;
gprr_relmcs = accRelMcs / nrm;
gprr_sc = accSC/nsel; gprr_bl = accBL/nsel;

f = figure('visible','off','Position',[0 0 1200 420]);
subplot(1,3,1);
plot(0:numel(B), [base gprr_chain], '-o'); grid on;
xlabel('# relays (chain 10/20/50/100 m)'); ylabel('GPRR within PDB'); ylim([0 1.02]);
title('Multi-relay chain'); xticks(0:numel(B));
subplot(1,3,2);
plot(relMcsList, gprr_relmcs, '-o'); grid on; hold on; yline(base,':');
xlabel('relay-link MCS'); ylabel('GPRR within PDB');
title(sprintf('Relay-MCS (leader MCS %d, x=%dm)', leadMcs, xrel));
subplot(1,3,3);
plot(xg, gprr_sc,'-o'); hold on; plot(xg, gprr_bl,'-s'); yline(base,':'); grid on;
xlabel('relay boundary x (m)'); ylabel('GPRR within PDB');
legend({'strict-closest','best-link'},'Location','best','FontSize',8);
title('Selection policy');
sgt(sprintf('%s — relay variants (leader MCS %d)', name, leadMcs));
save_fig(f, fullfile(figdir, sprintf('fig06_variants_%s.png', lower(name))));
end

% =====================================================================
function write_summary(Ri, Rm, datadir)
rows = {};
for k=1:numel(Ri.mcs)
    rows(end+1,:) = {'IVD', Ri.mcs(k), Ri.base(k), Ri.optx(k), max(Ri.gprr(:,k)), Ri.optgain(k)}; %#ok
end
for k=1:numel(Rm.mcs)
    rows(end+1,:) = {'Merge', Rm.mcs(k), Rm.base(k), Rm.optx(k), max(Rm.gprr(:,k)), Rm.optgain(k)}; %#ok
end
T = cell2table(rows, 'VariableNames', {'trace','mcs','base_gprr','opt_x_m','best_gprr','opt_gain'});
writetable(T, fullfile(datadir,'relay_placement_summary.csv'));
end

% =====================================================================
function sgt(s)
h = sgtitle(s); set(h,'Color','k','FontWeight','bold');
end

function save_fig(f, p)
set(f,'Color','w');
ax = findall(f,'type','axes');
set(ax,'Color','w','XColor','k','YColor','k','GridColor',[0.15 0.15 0.15]);
for a = ax.'
    a.Title.Color = 'k'; a.XLabel.Color = 'k'; a.YLabel.Color = 'k';
end
set(findall(f,'type','legend'),'Color','w','TextColor','k');
set(findall(f,'type','colorbar'),'Color','k');
st = findall(f,'type','text');                 % sgtitle and free text
set(st,'Color','k');
exportgraphics(f, p, 'Resolution', 140); close(f);
end
