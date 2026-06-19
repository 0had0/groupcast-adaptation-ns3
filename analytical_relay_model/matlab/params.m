function P = params()
% PARAMS  All resource-pool, traffic, channel and HARQ parameters as variables.
% Mirrors the ns-3 csi/relay_groupcast_eval configuration so the analytical
% model and the simulator share one parameterisation.

% ---- Numerology / frame ----
P.fc_GHz   = 5.9;            % carrier frequency
P.mu       = 1;             % numerology (30 kHz SCS)
P.scs_kHz  = 15 * 2^P.mu;    % subcarrier spacing
P.Tslot    = 1e-3 / 2^P.mu;  % slot duration (s) = 0.5 ms
P.slots_s  = 1e3 * 2^P.mu;   % slots per second  = 2000

% ---- Sidelink resource pool ----
P.Nsub     = 10;            % number of subchannels
P.W        = 10;            % subchannel size (PRBs)
P.PRB_band = P.Nsub * P.W;   % usable PRBs in the pool (=100)
P.bitmap   = [1 1 1 1 1 1 0 0 0 1 1 1];   % SL slot bitmap "111111000111"
P.rho      = sum(P.bitmap)/numel(P.bitmap);  % usable-slot fraction = 9/12

% PSFCH (HARQ feedback) timing
P.Npsfch       = 4;          % PSFCH period (slots)
P.MinGapPsfch  = 3;          % min slots PSSCH->PSFCH
P.psfch_oh     = 1/P.Npsfch; % fraction of SL slots reserved for PSFCH

% Available PSSCH subchannel-slots per second (data resources):
%   slots/s * usable-slot fraction * (1-PSFCH overhead) * #subchannels
P.Rpool = P.slots_s * P.rho * (1 - P.psfch_oh) * P.Nsub;   % subch-slots / s

% ---- HARQ ----
P.Nmax   = 2;               % max transmissions per TB (initial + retx)
P.cc_gain_dB = 3.0;         % chase-combining SINR gain per extra copy (modelled)

% HARQ round-trip (slots): PSSCH -> (>=MinGap, aligned to Npsfch) PSFCH ->
% decode/prepare -> retx. ~ ceil(MinGap/Npsfch)*Npsfch + processing.
P.harq_rtt_slots = ceil((P.MinGapPsfch)/P.Npsfch)*P.Npsfch + P.Npsfch;  % ~8 slots

% ---- PSSCH resource element budget (for TBS -> PRB mapping) ----
P.Nsym_data = 9;            % data OFDM symbols/slot after AGC/PSCCH/DMRS/PSFCH
P.RE_per_PRB = 12 * P.Nsym_data;   % data REs per PRB per slot

% ---- SB-SPS sensing-based scheduling ----
P.selWin_slots = 5;         % resource-selection window (slots)  (sim SetSlSelectionWindow)
P.RC = 3;                   % SlMaxNumPerReserve (reservations advertised)

% ---- SB-SPS resource (re)selection / sensing (collision model, §collision) ----
% Mode-2 sensing-based SPS (3GPP TS 38.214 §8.1.4). These drive collision_model.m.
P.reselCounter = 10;        % mean reselection counter RC (held TXs before reselect; [5,15]@100ms)
P.Pkeep        = 0.4;       % probability of keeping resource at counter expiry (0..0.8)
P.rKeepFrac    = 0.20;      % min fraction of candidate resources kept after RSRP exclusion (X%)
P.Tsens_s      = 1.1;       % sensing-window length (s)  (1100 ms full / 100 ms partial)
P.pSciDecode   = 0.90;      % per-period SCI decode prob within sensing range (for sensing reliability)
P.senseRange_m = 150;       % range at which mutual-sensing prob = 0.5 (sl-Thres-RSRP proxy)
P.vRel_mps     = 2.0;       % typical intra-platoon relative speed (for RC staleness term)
P.platoonSpacing_m = 20;    % nominal inter-vehicle spacing (for staleness shell density)
P.mTx          = 8;         % number of mutually-contending SB-SPS transmitters (platoon size)

% ---- Traffic (leader CAM-like flow) ----
P.payload_B = 800;          % application payload (bytes)
P.hdr_B     = 20;           % PDCP/RLC/MAC + SCI-2A overhead (bytes, calibrated)
P.RRI       = 10e-3;        % resource reservation interval (s)
P.PDB       = 10e-3;        % packet delay budget (s)
P.lambda    = 1/P.RRI;      % packets per second per flow

% ---- NR MCS table (index, Qm, code rate x1024, spectral eff bits/sym) ----
% 3GPP TS 38.214 Table 5.1.3.1-1 (subset on the swept ladder).
% gamma_req_dB = approx AWGN SINR for 10% BLER (calibrated offset added later).
%        idx   Qm    R*1024   SE      gamma_req_dB
mt = [   0     2     120     0.2344   -4.0 ;
         4     2     308     0.6016   -1.5 ;
         8     2     602     1.1758    2.0 ;
        12     4     434     1.6953    5.0 ;
        16     4     658     2.5703    8.5 ;
        20     6     517     3.0293   11.5 ;
        24     6     711     4.1660   15.0 ;
        28     6     873     5.1152   18.5 ];
P.mcs.idx   = mt(:,1);
P.mcs.Qm    = mt(:,2);
P.mcs.R     = mt(:,3)/1024;
P.mcs.SE    = mt(:,4);
P.mcs.gamma = mt(:,5);

% ---- Channel (3GPP TR 37.885 V2V Highway) ----
% LOS path loss: PL = 32.4 + 20log10(d[m]) + 20log10(fc[GHz])
P.PL0   = 32.4 + 20*log10(P.fc_GHz);
P.PLexp = 20;               % 20 log10(d)
% Free (calibrated) parameters — fitted to simulated PRR(d):
P.SNR0_dB   = 64.0;         % effective (Ptx - N0 - margin) so SINR=SNR0-PL at d=1
P.Lb0_dB    = 6.0;          % first-blocker NLOSv excess loss
P.LbStep_dB = 4.0;          % each additional blocker excess loss
P.bler_sigma= 1.6;          % BLER transition width (dB)
P.shadow_dB = 3.0;          % log-normal shadowing std (informational)

% ---- Geometry (vehicle-as-blocker) ----
P.vehL = 4.5;               % blocker length (m)  (sim blockerL)
P.vehW = 2.0;               % blocker width (m)   (sim blockerW)

% ---- Relay defaults ----
P.relay_broadcast = true;   % relay link Broadcast (no PSFCH)
P.relay_Nmax = 2;
end
