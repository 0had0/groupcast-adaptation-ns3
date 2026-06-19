function S = load_trace(path, dt_sample)
% LOAD_TRACE  Read a SUMO .mob.csv (time,node,x,y,speed) into matrices.
%   S.t   [1 x Nt]      sampled time instants (s)
%   S.id  [1 x Nn]      node ids (sorted; leader id 0 first if present)
%   S.X,S.Y [Nt x Nn]   positions (NaN where node absent at that time)
%   S.spd  [Nt x Nn]    speed
%   S.leadCol           column index of leader (node id 0)
% dt_sample : subsample interval in seconds (default 1.0) to bound cost.

if nargin < 2, dt_sample = 1.0; end
R = readmatrix(path, 'NumHeaderLines', 1);   % cols: time node x y speed
t = R(:,1); nid = R(:,2); x = R(:,3); y = R(:,4); spd = R(:,5);

ids = unique(nid);                 ids = sort(ids(:)).';
tmax = max(t);
ts = 0:dt_sample:tmax;             % sampled grid
Nt = numel(ts); Nn = numel(ids);
X = nan(Nt,Nn); Y = nan(Nt,Nn); V = nan(Nt,Nn);

% index helpers
[~, idCol] = ismember(nid, ids);
% snap each row's time to nearest sample bin
tbin = round(t / dt_sample) + 1;
keep = tbin >= 1 & tbin <= Nt;
lin = sub2ind([Nt Nn], tbin(keep), idCol(keep));
X(lin) = x(keep); Y(lin) = y(keep); V(lin) = spd(keep);

S.t = ts; S.id = ids; S.X = X; S.Y = Y; S.spd = V;
lc = find(ids==0,1); if isempty(lc), lc = 1; end
S.leadCol = lc;
S.path = path;
end
