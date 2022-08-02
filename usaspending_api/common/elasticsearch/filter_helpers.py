def create_fiscal_year_filter(year):
    return [{"start_date": f"{int(year) - 1}-10-01", "end_date": f"{year}-09-30"}]
