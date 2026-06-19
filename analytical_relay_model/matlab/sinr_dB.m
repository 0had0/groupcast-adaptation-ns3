function g = sinr_dB(P, d, n)
% SINR_DB  Received SINR (dB) at distance d (m) behind n NLOSv blockers.
%   SINR = SNR0 - PL_LOS(d) - L_nlosv(n)
% 3GPP TR 37.885 highway LOS path loss; per-blocker excess loss is the
% calibrated NLOSv term. d, n may be arrays (same size).
d = max(d, 1);
PL = P.PL0 + P.PLexp .* log10(d);
Lnl = (n > 0) .* (P.Lb0_dB + max(n - 1, 0) .* P.LbStep_dB);
g = P.SNR0_dB - PL - Lnl;
end
