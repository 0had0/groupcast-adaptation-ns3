function b = bler_mcs(P, sinrdB, k)
% BLER_MCS  Block error rate for MCS position k (1..numel(P.mcs.idx)) at the
% given SINR (dB). Smooth AWGN-link approximation centred on the per-MCS
% required SINR with calibrated transition width sigma:
%   BLER = 0.5 * erfc( (SINR - gamma_req(k)) / (sqrt(2) sigma) )
g = P.mcs.gamma(k);
b = 0.5 * erfc( (sinrdB - g) ./ (sqrt(2) * P.bler_sigma) );
b = min(max(b, 0), 1);
end
