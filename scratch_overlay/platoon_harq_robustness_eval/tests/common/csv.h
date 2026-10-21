// csv.h -- CSV / CSV.GZ reader by column name.
//
// Every column is stored as double (NaN where a cell is not a number); columns whose first data
// row is not numeric (scenario, traffic, event letters, ...) are additionally kept as strings.
#pragma once

#include <cmath>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace ptest
{

class Table
{
  public:
    std::vector<std::string> cols;
    std::unordered_map<std::string, int> idx;
    size_t nrows{0};
    std::vector<std::vector<double>> num; //!< [col][row]
    std::vector<std::vector<std::string>> str; //!< [col][row], empty vector for numeric columns

    bool Has(const std::string& c) const { return idx.count(c) > 0; }
    /// numeric column (aborts the process with a message if absent)
    const std::vector<double>& D(const std::string& c) const;
    const std::vector<std::string>& S(const std::string& c) const;
    double At(const std::string& c, size_t row) const;
    std::string StrAt(const std::string& c, size_t row) const;
    /// value in row 0 (run.csv has a single row); NaN if the column is absent
    double R0(const std::string& c, double def = NAN) const;
    std::string R0s(const std::string& c) const;
    bool Empty() const { return nrows == 0; }
};

/// Resolve "<path>" or "<path>.gz" (whichever exists). Returns "" if neither.
std::string ResolveCsv(const std::string& path);

/// Load a CSV (plain or .gz, transparent). Returns false if the file is missing (t is left empty).
bool LoadTable(const std::string& path, Table& t);

/// Split a line on commas (no quoting).
std::vector<std::string> SplitCsv(const std::string& line);

/// Whole (decompressed) text of a CSV / CSV.GZ file ("" if missing). Resolves "<path>" or "<path>.gz".
std::string ReadText(const std::string& path);

/// Compare two result tables cell by cell as text (header + rows). Columns in `exclude` are skipped.
/// Returns the number of differing cells; `firstDiff` receives "<file>: row r col c: a vs b" for the first one.
long CompareTables(const std::string& pathA, const std::string& pathB, const std::vector<std::string>& exclude,
                   std::string* firstDiff, bool* missing);

/// Write helpers
class CsvWriter
{
  public:
    explicit CsvWriter(const std::string& path, const std::string& header);
    ~CsvWriter();
    void Row(const std::vector<std::string>& cells);
    bool Ok() const { return m_f != nullptr; }

  private:
    FILE* m_f{nullptr};
};

} // namespace ptest
