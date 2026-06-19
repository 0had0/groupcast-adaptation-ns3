function C = collision_model(P, x, n, o)
% COLLISION_MODEL  Analytical SB-SPS resource-collision probability between a
% TX node i and its relay node j separated by distance x (m), behind n NLOSv
% blockers, when *all* nodes run NR-SL Mode-2 sensing-based SPS (TS 38.214
% §8.1.4). This is the reduced-form companion to prr_cal.m / the relay study:
% it quantifies why an *uncoordinated* relay lowers group PRR.
%
%   C = collision_model(P, x, n, o)
% x,n may be arrays (same size); o is an optional struct overriding any of the
% swept knobs {B, PDB, RRI, Tsens, RC, m, mcs} (defaults from P / params.m).
%
% Returns a struct of per-resource collision probabilities (same shape as x):
%   C.pair   pairwise i<->j collision prob (the elementary event asked for)
%   C.agg    aggregate prob a given grant collides with >=1 of the m-1 others
%   C.hidden hidden-node component of the pair (sensing failed)
%   C.sensed in-range residual (concurrent-reselection) component of the pair
%   C.stale  reselection-counter staleness (mobility) component of the pair
%   C.psEff  effective mutual-sensing probability at x (after sensing-window rel.)
% and scalars describing the resource grid:
%   C.cm  sub-channels/TB   C.M  #orthogonal candidate resources   C.p0 per-pair
%   C.Khid expected #hidden contenders   C.a reselection rate   C.Lburst burst len
%
% --------------------------------------------------------------------------
% MODEL (all terms reuse the calibrated channel g(d,n)=alpha*log10 d + beta*n):
%
%  Resource grid.  A TB of B bytes at the chosen MCS occupies c_m sub-channels.
%  The selection window is bounded by BOTH the packet-delay budget and the
%  reservation interval (a grant must fire every RRI), W = floor(min(PDB,RRI)/Tslot),
%  of which only a fraction rho carry SL.  Orthogonal candidate resources:
%       M = floor(W*rho) * floor(Nsub/c_m).
%  Two transmitters choosing independently collide on a resource with
%       p0 = (1/floor(W*rho)) * min(1,(2 c_m-1)/Nsub)        (same slot x overlap).
%
%  Sensing.  j senses i (and excludes its reservation) when the effective SNR
%  SNR0-g(x) exceeds the sensing threshold; with shadowing this is
%       p_sense(x) = 1/2 erfc( (g(x)-theta_sens)/(sqrt2 sigma) ),
%  theta_sens = alpha*log10(senseRange).  A short sensing window observes fewer
%  periods Nobs=floor(Tsens/RRI), so reservations are detected with reliability
%       eta = 1-(1-pSciDecode)^Nobs,   psEff = p_sense*eta.
%
%  Pairwise collision.  If i,j are hidden (prob 1-psEff) they pick independently
%  -> p0.  If mutually sensed they only clash by reselecting concurrently in an
%  overlapping window -> a*p0, with reselection rate a=(1-Pkeep)/RC.  A finite RC
%  also lets relative motion turn a sensed neighbour stale within the hold:
%       p_stale = clamp( vRel*RC*RRI / spacing ) * p0.
%       C.pair = (1-psEff)*p0 + psEff*a*p0 + p_stale.
%
%  Aggregate.  A victim sees Khid = (m-1)*(1-<psEff>) hidden contenders, so the
%  probability its grant is hit at least once is 1-(1-p0)^Khid plus the in-range
%  residual; this is the quantity that multiplies (1-PRR_link) and that grows
%  every time an uncoordinated relay adds a transmitter.
% --------------------------------------------------------------------------

if nargin < 4, o = struct(); end
B    = pick(o,'B',    P.payload_B);
PDB  = pick(o,'PDB',  P.PDB);
RRI  = pick(o,'RRI',  P.RRI);
Tsens= pick(o,'Tsens',P.Tsens_s);
RC   = pick(o,'RC',   P.reselCounter);
m    = pick(o,'m',    P.mTx);
mcs  = pick(o,'mcs',  16);

k  = find(P.mcs.idx==mcs,1); if isempty(k), k = find(P.mcs.idx>=mcs,1); end
SE = P.mcs.SE(k);

% ---- resource footprint of one TB ----
bits   = (B + P.hdr_B)*8;
PRBraw = ceil(bits ./ (SE * P.RE_per_PRB));
cm     = max(1, ceil(PRBraw / P.W));            % sub-channels per TB
cm     = min(cm, P.Nsub);

% ---- candidate-resource grid (selection window bounded by PDB AND RRI) ----
Wsel_slots = floor(min(PDB,RRI) / P.Tslot);
usable     = max(1, floor(Wsel_slots * P.rho)); % SL slots in the window
Mfreq      = max(1, floor(P.Nsub / cm));        % orthogonal freq positions
M          = usable * Mfreq;                    % orthogonal candidate resources
pf_overlap = min(1, (2*cm - 1) / P.Nsub);       % P(freq overlap | same slot)
p0         = (1/usable) * pf_overlap;           % per-pair same-resource prob

% ---- effective mutual-sensing probability at x ----
g        = P.PLexp .* log10(max(x,1)) + P.Cblock .* n;
theta_s  = P.PLexp * log10(P.senseRange_m);
sigma    = max(P.shadow_dB, 1e-3);
p_sense  = 0.5 * erfc( (g - theta_s) ./ (sqrt(2)*sigma) );
Nobs     = max(1, floor(Tsens / RRI));
eta      = 1 - (1 - P.pSciDecode).^Nobs;        % sensing-window reliability
psEff    = p_sense .* eta;

% ---- reselection dynamics ----
a       = (1 - P.Pkeep) / max(RC,1);            % reselection prob per packet
Lburst  = RC / (2*(1 - P.Pkeep));               % mean collided-packet run length
Delta   = P.vRel_mps * RC * RRI;                % displacement during one hold (m)
fStale  = min(1, Delta / max(P.platoonSpacing_m,1e-3));

% ---- pairwise (i<->j) collision ----
C.hidden = (1 - psEff) .* p0;
C.sensed = psEff .* a .* p0;
C.stale  = fStale .* p0 .* ones(size(g));
C.pair   = C.hidden + C.sensed + C.stale;

% ---- aggregate (one grant vs the other m-1 SB-SPS transmitters) ----
Khid     = (m - 1) .* (1 - mean(psEff(:)));     % expected hidden contenders
C.Khid   = Khid;
C.agg    = 1 - (1 - p0).^max(Khid,0) ...        % hidden hits
           + (m - 1) .* a .* p0;                % in-range residual (small)
C.agg    = min(C.agg, 1);

% ---- scalars ----
C.psEff = psEff; C.cm = cm; C.M = M; C.p0 = p0; C.a = a;
C.Lburst = Lburst; C.usable = usable; C.Mfreq = Mfreq; C.eta = eta;
end

function v = pick(o, name, d)
if isstruct(o) && isfield(o, name) && ~isempty(o.(name)), v = o.(name); else, v = d; end
end
