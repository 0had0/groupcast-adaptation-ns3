#include "common/csv.h"

#include "common/harness.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>

namespace ptest
{

const std::vector<double>&
Table::D(const std::string& c) const
{
    auto it = idx.find(c);
    if (it == idx.end())
    {
        std::cerr << "csv: missing column '" << c << "'\n";
        std::abort();
    }
    return num[it->second];
}

const std::vector<std::string>&
Table::S(const std::string& c) const
{
    auto it = idx.find(c);
    if (it == idx.end())
    {
        std::cerr << "csv: missing column '" << c << "'\n";
        std::abort();
    }
    return str[it->second];
}

double
Table::At(const std::string& c, size_t row) const
{
    return D(c)[row];
}

std::string
Table::StrAt(const std::string& c, size_t row) const
{
    auto it = idx.find(c);
    if (it == idx.end())
    {
        return "";
    }
    if (!str[it->second].empty())
    {
        return str[it->second][row];
    }
    return Fmt(num[it->second][row], 12);
}

double
Table::R0(const std::string& c, double def) const
{
    auto it = idx.find(c);
    if (it == idx.end() || nrows == 0)
    {
        return def;
    }
    return num[it->second][0];
}

std::string
Table::R0s(const std::string& c) const
{
    if (nrows == 0)
    {
        return "";
    }
    return StrAt(c, 0);
}

std::string
ResolveCsv(const std::string& path)
{
    if (FileExists(path))
    {
        return path;
    }
    if (FileExists(path + ".gz"))
    {
        return path + ".gz";
    }
    return "";
}

std::vector<std::string>
SplitCsv(const std::string& line)
{
    std::vector<std::string> out;
    size_t s = 0;
    while (true)
    {
        size_t e = line.find(',', s);
        if (e == std::string::npos)
        {
            out.push_back(line.substr(s));
            break;
        }
        out.push_back(line.substr(s, e - s));
        s = e + 1;
    }
    return out;
}

static bool
ParseNum(const char* s, double& v)
{
    if (!*s)
    {
        return false;
    }
    char* end = nullptr;
    v = std::strtod(s, &end);
    return end != s && *end == '\0';
}

bool
LoadTable(const std::string& pathIn, Table& t)
{
    t = Table();
    const std::string path = ResolveCsv(pathIn);
    if (path.empty())
    {
        return false;
    }
    const bool gz = path.size() > 3 && path.compare(path.size() - 3, 3, ".gz") == 0;
    const std::string cmd = gz ? "gzip -dc '" + path + "'" : "cat '" + path + "'";
    FILE* fp = popen(cmd.c_str(), "r");
    if (!fp)
    {
        return false;
    }
    char* line = nullptr;
    size_t cap = 0;
    ssize_t len;
    bool first = true;
    std::vector<bool> isStr;
    std::vector<std::string> cells;
    while ((len = getline(&line, &cap, fp)) > 0)
    {
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
        {
            line[--len] = '\0';
        }
        if (len == 0)
        {
            continue;
        }
        if (line[0] == '#')
        {
            continue;
        }
        if (first)
        {
            t.cols = SplitCsv(line);
            for (size_t i = 0; i < t.cols.size(); ++i)
            {
                t.idx[t.cols[i]] = static_cast<int>(i);
            }
            t.num.assign(t.cols.size(), {});
            t.str.assign(t.cols.size(), {});
            first = false;
            continue;
        }
        // fast split
        const size_t nc = t.cols.size();
        if (t.nrows == 0)
        {
            isStr.assign(nc, false);
        }
        char* p = line;
        for (size_t c = 0; c < nc; ++c)
        {
            char* comma = nullptr;
            if (*p == '"')
            {
                // RFC 4180 quoted field (e.g. the cmd column of campaign manifests): the field ends at the closing quote
                char* q = p + 1;
                while (*q && !(*q == '"' && q[1] != '"'))
                {
                    q += (*q == '"') ? 2 : 1;
                }
                if (*q == '"')
                {
                    *q = '\0';
                    ++p; // drop the opening quote
                    comma = (q[1] == ',') ? q + 1 : nullptr;
                }
                else
                {
                    comma = std::strchr(p, ',');
                    if (comma)
                    {
                        *comma = '\0';
                    }
                }
            }
            else
            {
                comma = std::strchr(p, ',');
                if (comma)
                {
                    *comma = '\0';
                }
            }
            double v = NAN;
            bool ok = ParseNum(p, v);
            if (t.nrows == 0)
            {
                isStr[c] = !ok && *p != '\0';
            }
            t.num[c].push_back(ok ? v : NAN);
            if (isStr[c])
            {
                t.str[c].emplace_back(p);
            }
            if (!comma)
            {
                // fewer cells than header: pad
                for (size_t c2 = c + 1; c2 < nc; ++c2)
                {
                    t.num[c2].push_back(NAN);
                    if (t.nrows == 0)
                    {
                        isStr[c2] = false;
                    }
                    if (isStr[c2])
                    {
                        t.str[c2].emplace_back();
                    }
                }
                break;
            }
            p = comma + 1;
        }
        ++t.nrows;
    }
    free(line);
    pclose(fp);
    return true;
}

std::string
ReadText(const std::string& pathIn)
{
    const std::string path = ResolveCsv(pathIn);
    if (path.empty())
    {
        return "";
    }
    const bool gz = path.size() > 3 && path.compare(path.size() - 3, 3, ".gz") == 0;
    const std::string cmd = gz ? "gzip -dc '" + path + "'" : "cat '" + path + "'";
    FILE* fp = popen(cmd.c_str(), "r");
    std::string out;
    if (!fp)
    {
        return out;
    }
    char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, fp)) > 0)
    {
        out.append(buf, n);
    }
    pclose(fp);
    return out;
}

static std::vector<std::string>
Lines(const std::string& txt)
{
    std::vector<std::string> v;
    size_t s = 0;
    while (s < txt.size())
    {
        size_t e = txt.find('\n', s);
        if (e == std::string::npos)
        {
            e = txt.size();
        }
        v.push_back(txt.substr(s, e - s));
        s = e + 1;
    }
    return v;
}

long
CompareTables(const std::string& pathA, const std::string& pathB, const std::vector<std::string>& exclude,
              std::string* firstDiff, bool* missing)
{
    if (ResolveCsv(pathA).empty() || ResolveCsv(pathB).empty())
    {
        if (missing)
        {
            *missing = true;
        }
        return 0;
    }
    if (missing)
    {
        *missing = false;
    }
    const auto la = Lines(ReadText(pathA)), lb = Lines(ReadText(pathB));
    long nd = 0;
    if (la.empty() || lb.empty())
    {
        return la.size() == lb.size() ? 0 : 1;
    }
    const auto ha = SplitCsv(la[0]), hb = SplitCsv(lb[0]);
    if (ha != hb)
    {
        if (firstDiff && firstDiff->empty())
        {
            *firstDiff = "header differs";
        }
        return 1;
    }
    std::vector<bool> skip(ha.size(), false);
    for (size_t c = 0; c < ha.size(); ++c)
    {
        for (const auto& x : exclude)
        {
            if (ha[c] == x)
            {
                skip[c] = true;
            }
        }
    }
    if (la.size() != lb.size())
    {
        ++nd;
        if (firstDiff && firstDiff->empty())
        {
            *firstDiff = "row count " + std::to_string(la.size() - 1) + " vs " + std::to_string(lb.size() - 1);
        }
    }
    const size_t n = std::min(la.size(), lb.size());
    for (size_t r = 1; r < n; ++r)
    {
        if (la[r] == lb[r])
        {
            continue;
        }
        const auto ca = SplitCsv(la[r]), cb = SplitCsv(lb[r]);
        for (size_t c = 0; c < std::min(ca.size(), cb.size()); ++c)
        {
            if (c < skip.size() && skip[c])
            {
                continue;
            }
            if (ca[c] != cb[c])
            {
                ++nd;
                if (firstDiff && firstDiff->empty())
                {
                    *firstDiff = "row " + std::to_string(r) + " col " + ha[c] + ": " + ca[c] + " vs " + cb[c];
                }
            }
        }
    }
    return nd;
}

CsvWriter::CsvWriter(const std::string& path, const std::string& header)
{
    m_f = std::fopen(path.c_str(), "w");
    if (m_f)
    {
        std::fprintf(m_f, "%s\n", header.c_str());
    }
}

CsvWriter::~CsvWriter()
{
    if (m_f)
    {
        std::fclose(m_f);
    }
}

void
CsvWriter::Row(const std::vector<std::string>& cells)
{
    if (!m_f)
    {
        return;
    }
    for (size_t i = 0; i < cells.size(); ++i)
    {
        std::fprintf(m_f, "%s%s", i ? "," : "", cells[i].c_str());
    }
    std::fprintf(m_f, "\n");
}

} // namespace ptest
