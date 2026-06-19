function run_collision()
% RUN_COLLISION  SB-SPS resource-collision study for the uncoordinated-relay
% hypothesis: "uncoordinated relay UEs reduce PRR in a vehicle platoon because
% of resource collisions when every node runs sensing-based SPS, modulated by
% the traffic (size, periodicity, PDB) and the SPS knobs (sensing window,
% reselection counter, selection window)."
%
% Produces:
%   figures/fig07_collision_sweeps.png  -- p_col vs {distance, packet size, PDB,
%       RRI, sensing window, reselection counter} (+ burst length).
%   figures/fig08_collision_heatmaps.png-- p_col over (distance x RRI) and
%       (distance x packet size) planes.
%   figures/fig09_relay_prr_impact.png  -- group PRR vs #uncoordinated relays
%       over the real IVD trace geometry (the hypothesis figure).
%   data/collision_summary.csv          -- key operating points.

here = fileparts(mfilename('fullpath'));
root = fileparts(here);
figdir  = fullfile(root,'figures'); if ~exist(figdir,'dir'), mkdir(figdir); end
datadir = fullfile(root,'data');
set(groot,'defaultFigureColor','w');
try, theme(groot,'light'); catch, end

P = params();
% adopt the fitted reduced-form channel so sensing ranges are consistent with PRR
P = adopt_calibrated(P, datadir);

xref = 40;            % reference TX->relay distance for the non-distance sweeps (m)
nref = 0;            % blockers along that link (single-lane platoon ~ 0)
mcs  = 16;

% =====================================================================
% FIG 7 : one-factor-at-a-time sweeps
% =====================================================================
f = figure('visible','off','Position',[0 0 1280 760]);

% (1) distance --------------------------------------------------------
xg = 1:2:200;
Cx = collision_model(P, xg, zeros(size(xg)), struct('mcs',mcs));
ax = subplot(2,3,1); hold(ax,'on');
plot(xg, Cx.pair*100, '-', 'LineWidth',1.8);
plot(xg, Cx.hidden*100, '--');
plot(xg, Cx.sensed*100, ':');
plot(xg, (1-Cx.psEff)*100, '-.', 'Color',[.5 .5 .5]);
grid on; xlabel('TX\rightarrowrelay distance x (m)'); ylabel('collision prob (%)');
title('vs distance: hidden-node growth'); ylim([0 max(1,max(Cx.pair*100))*1.1]);
legend({'pair p_{col}','hidden term','sensed residual','1-p_{sense}(x)'}, ...
       'Location','northwest','FontSize',7);

% (2) packet size -----------------------------------------------------
Bg = 100:50:1500;
pp = arrayfun(@(b) getfield(collision_model(P,xref,nref,struct('B',b,'mcs',mcs)),'pair'), Bg); %#ok
cm = arrayfun(@(b) getfield(collision_model(P,xref,nref,struct('B',b,'mcs',mcs)),'cm'),   Bg); %#ok
ax = subplot(2,3,2); yyaxis left; plot(Bg, pp*100,'-','LineWidth',1.8);
ylabel('collision prob (%)'); yyaxis right; stairs(Bg, cm,'-');
ylabel('sub-channels / TB c_m');
grid on; xlabel('payload B (bytes)');
title(sprintf('vs packet size (x=%dm)',xref));

% (3) PDB -------------------------------------------------------------
PDBg = (2:1:50)*1e-3;
pp = arrayfun(@(t) getfield(collision_model(P,xref,nref,struct('PDB',t,'mcs',mcs)),'pair'), PDBg); %#ok
MM = arrayfun(@(t) getfield(collision_model(P,xref,nref,struct('PDB',t,'mcs',mcs)),'M'),    PDBg); %#ok
ax = subplot(2,3,3); yyaxis left; plot(PDBg*1e3, pp*100,'-','LineWidth',1.8);
ylabel('collision prob (%)'); yyaxis right; plot(PDBg*1e3, MM,'-');
ylabel('# candidate resources M');
grid on; xlabel('packet delay budget PDB (ms)');
title(sprintf('vs PDB (x=%dm)',xref));

% (4) RRI -------------------------------------------------------------
RRIg = (5:5:200)*1e-3;
ppP = arrayfun(@(r) getfield(collision_model(P,xref,nref,struct('RRI',r,'mcs',mcs)),'pair'),RRIg); %#ok
ppA = arrayfun(@(r) getfield(collision_model(P,xref,nref,struct('RRI',r,'mcs',mcs)),'agg'), RRIg); %#ok
ax = subplot(2,3,4); hold(ax,'on');
plot(RRIg*1e3, ppP*100,'-','LineWidth',1.8);
plot(RRIg*1e3, ppA*100,'-','LineWidth',1.8);
grid on; xlabel('reservation interval RRI (ms)'); ylabel('collision prob (%)');
title('vs RRI (load & window)'); legend({'pair','aggregate (m TX)'},'Location','northeast','FontSize',7);

% (5) sensing window --------------------------------------------------
Tg = [10 20 50 100 200 400 700 1100 2200]*1e-3;
ppP = arrayfun(@(t) getfield(collision_model(P,xref,nref,struct('Tsens',t,'mcs',mcs)),'pair'),Tg); %#ok
ppA = arrayfun(@(t) getfield(collision_model(P,xref,nref,struct('Tsens',t,'mcs',mcs)),'agg'), Tg); %#ok
ax = subplot(2,3,5); hold(ax,'on');
semilogx(Tg*1e3, ppP*100,'-o','LineWidth',1.6);
semilogx(Tg*1e3, ppA*100,'-s','LineWidth',1.6);
set(ax,'XScale','log'); grid on;
xlabel('sensing-window length T_{sens} (ms)'); ylabel('collision prob (%)');
title(sprintf('vs sensing window (x=%dm)',xref));
legend({'pair','aggregate'},'Location','northeast','FontSize',7);

% (6) reselection counter --------------------------------------------
RCg = 1:1:30;
ppP = arrayfun(@(rc) getfield(collision_model(P,xref,nref,struct('RC',rc,'mcs',mcs)),'pair'),RCg); %#ok
Lb  = arrayfun(@(rc) getfield(collision_model(P,xref,nref,struct('RC',rc,'mcs',mcs)),'Lburst'),RCg); %#ok
ax = subplot(2,3,6); yyaxis left; plot(RCg, ppP*100,'-','LineWidth',1.8);
ylabel('collision prob (%)'); yyaxis right; plot(RCg, Lb,'-');
ylabel('mean collided-run length (pkts)');
grid on; xlabel('reselection counter RC');
title('vs RC: mean ~flat, bursts grow');

sgt('SB-SPS pairwise/aggregate resource-collision sensitivity (relay node j at distance x)');
save_fig(f, fullfile(figdir,'fig07_collision_sweeps.png'));

% =====================================================================
% FIG 8 : 2-D collision planes
% =====================================================================
f = figure('visible','off','Position',[0 0 1200 460]);
xg = 1:3:200;
% (a) distance x RRI
RRIg = (5:5:150)*1e-3;
Zr = zeros(numel(RRIg), numel(xg));
for ir=1:numel(RRIg)
    C = collision_model(P, xg, zeros(size(xg)), struct('RRI',RRIg(ir),'mcs',mcs));
    Zr(ir,:) = C.pair*100;
end
ax=subplot(1,2,1); imagesc(xg, RRIg*1e3, Zr); set(ax,'YDir','normal');
xlabel('distance x (m)'); ylabel('RRI (ms)'); c=colorbar; c.Label.String='pair p_{col} (%)';
title('collision over (distance, RRI)');
% (b) distance x packet size
Bg = 100:50:1500;
Zb = zeros(numel(Bg), numel(xg));
for ib=1:numel(Bg)
    C = collision_model(P, xg, zeros(size(xg)), struct('B',Bg(ib),'mcs',mcs));
    Zb(ib,:) = C.pair*100;
end
ax=subplot(1,2,2); imagesc(xg, Bg, Zb); set(ax,'YDir','normal');
xlabel('distance x (m)'); ylabel('payload B (bytes)'); c=colorbar; c.Label.String='pair p_{col} (%)';
title('collision over (distance, packet size)');
sgt('Collision probability planes (sensed near the TX, hidden far)');
save_fig(f, fullfile(figdir,'fig08_collision_heatmaps.png'));

% =====================================================================
% FIG 9 : the hypothesis -- group PRR drops as uncoordinated relays are added
% =====================================================================
S  = load_trace(fullfile(datadir,'platoon_ivd_sweep.mob.csv'), 1.0);
PG = allpair_geom(P, S);
lc = S.leadCol;
D  = squeeze(PG.D(lc,:,:)).';  Nb = squeeze(PG.N(lc,:,:)).';   % Nt x Nn leader links
mask = ~isnan(D) & D>0;
d = D(mask); nb = Nb(mask);
kL = find(P.mcs.idx==mcs,1);

prrLink = prr_cal(P, d, nb, kL);            % collision-free link reliability
mBase   = P.mTx;                             % leader-only contender baseline
nRelay  = 0:6;
gprr_dir = zeros(size(nRelay));             % direct link only, with collisions
gprr_coop= zeros(size(nRelay));             % direct OR relay path, with collisions
for q = 1:numel(nRelay)
    m = mBase + nRelay(q);                   % each uncoordinated relay = +1 TX
    Cd = collision_model(P, d, nb, struct('m',m,'mcs',mcs));
    pSucDirect = prrLink .* (1 - Cd.agg);
    gprr_dir(q) = mean(pSucDirect);
    % cooperative gain of the *added* relays (geom: relay ~ mid-platoon, x~30m),
    % but the relay hop ALSO suffers the same collision floor.
    if nRelay(q)==0
        gprr_coop(q) = mean(pSucDirect);
    else
        Crel = collision_model(P, 30, 0, struct('m',m,'mcs',mcs));
        prrRelayHop = prr_cal(P, 30, 0, kL) * (1-Crel.agg);   % leader->relay
        % relay->far receiver (assume relay halves residual distance)
        prrR2F = prr_cal(P, max(d-30,1), nb, kL) .* (1 - Cd.agg);
        coopPath = prrRelayHop .* prrR2F .* (d>30);
        pSuc = 1 - (1-pSucDirect).*(1-coopPath);
        gprr_coop(q) = mean(pSuc);
    end
end

f = figure('visible','off','Position',[0 0 1180 460]);
subplot(1,2,1); hold on;
plot(nRelay, gprr_dir,'-o','LineWidth',1.8);
plot(nRelay, gprr_coop,'-s','LineWidth',1.8);
yline(mean(prrLink),':k','collision-free link');
grid on; xlabel('# uncoordinated relays added'); ylabel('group PRR (IVD geometry)');
ylim([0 1.02]); title(sprintf('Group PRR vs added SB-SPS relays (MCS %d)',mcs));
legend({'direct link (collision-loaded)','direct OR relay (coop)','collision-free ceiling'}, ...
       'Location','best','FontSize',8);

% collision floor vs contenders
subplot(1,2,2); hold on;
mg = 1:1:20;
pcOld = 1 - (1 - 1/(P.Nsub*round(P.selWin_slots*P.rho))).^(mg-1);   % old fig03 model
pcNew = arrayfun(@(mm) mean( getf(collision_model(P,d,nb,struct('m',mm,'mcs',mcs)),'agg') ), mg);
plot(mg, pcNew*100,'-o','LineWidth',1.8);
plot(mg, pcOld*100,'--','LineWidth',1.2);
grid on; xlabel('# simultaneous SB-SPS transmitters m'); ylabel('mean aggregate collision (%)');
title('Collision floor vs contenders'); legend({'this model (sensing+geometry)','fig03 baseline 1-(1-1/N_{csr})^{m-1}'}, ...
       'Location','northwest','FontSize',8);
sgt('Hypothesis: each uncoordinated SB-SPS relay raises the collision floor and can erode group PRR');
save_fig(f, fullfile(figdir,'fig09_relay_prr_impact.png'));

% =====================================================================
% summary CSV
% =====================================================================
rows = {};
addrow = @(lbl,val) {lbl, val};
ops = { ...
  'pair_col_x20_pct',  collision_model(P,20,0,struct('mcs',mcs)).pair*100; ...
  'pair_col_x40_pct',  collision_model(P,40,0,struct('mcs',mcs)).pair*100; ...
  'pair_col_x100_pct', collision_model(P,100,0,struct('mcs',mcs)).pair*100; ...
  'cm_subch_800B',     collision_model(P,40,0,struct('mcs',mcs)).cm; ...
  'M_candidates',      collision_model(P,40,0,struct('mcs',mcs)).M; ...
  'p0_per_pair_pct',   collision_model(P,40,0,struct('mcs',mcs)).p0*100; ...
  'agg_col_m8_pct',    mean(getf(collision_model(P,d,nb,struct('m',8,'mcs',mcs)),'agg'))*100; ...
  'agg_col_m12_pct',   mean(getf(collision_model(P,d,nb,struct('m',12,'mcs',mcs)),'agg'))*100; ...
  'gprr_0relay',       gprr_dir(1); ...
  'gprr_3relay_direct',gprr_dir(4); ...
  'gprr_3relay_coop',  gprr_coop(4); ...
};
T = cell2table(ops, 'VariableNames', {'metric','value'});
writetable(T, fullfile(datadir,'collision_summary.csv'));
fprintf('DONE. fig07/08/09 in %s ; collision_summary.csv in %s\n', figdir, datadir);
disp(T);
end

% =====================================================================
function P = adopt_calibrated(P, datadir)
fp = fullfile(datadir,'calibrated_params.txt');
if ~exist(fp,'file'), P.thr = 40*ones(1,numel(P.mcs.idx)); P.sk = 4*ones(1,numel(P.mcs.idx)); return; end
L = readlines(fp);
for i=1:numel(L)
    s = strtrim(L(i));
    if startsWith(s,'PLexp='),  P.PLexp  = sscanf(extractAfter(s,'='),'%f'); end
    if startsWith(s,'Cblock'),  P.Cblock = sscanf(extractAfter(s,'='),'%f'); end
    if startsWith(s,'thr_k'),   P.thr = sscanf(extractAfter(s,'='),'%f').'; end
    if startsWith(s,'s_k'),     P.sk  = sscanf(extractAfter(s,'='),'%f').'; end
end
end

function v = getf(C,f), v = C.(f); end

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
set(findall(f,'type','text'),'Color','k');
exportgraphics(f, p, 'Resolution', 140); close(f);
end
