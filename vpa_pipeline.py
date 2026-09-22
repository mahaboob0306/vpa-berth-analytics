import os
import numpy as np
import pandas as pd

DOWNLOADS_DIR = r"C:\Users\VPA-ITC-05\Downloads"
CSV_FILE = os.path.join(DOWNLOADS_DIR, "Berth Throughput.csv")
OUTPUT_EXCEL = os.path.join(r"C:\VPA-Noor", "Berth_Performance_Report_Last_3_Months.xlsx")

def run_pipeline():
    if not os.path.exists(CSV_FILE):
        print(f"Error: Could not find '{CSV_FILE}'.")
        return

    print(f"Reading: {CSV_FILE}")
    try:
        df = pd.read_csv(CSV_FILE, encoding="utf-8", low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(CSV_FILE, encoding="cp1252", low_memory=False)

    # Clean headers and deduplicate
    df.columns = [str(col).strip() for col in df.columns]
    seen = {}
    unique_cols = []
    for col in df.columns:
        if col in seen:
            seen[col] += 1
            unique_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            unique_cols.append(col)
    df.columns = unique_cols

    # Standardize column names to snake_case
    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(" ", "_")
        .str.replace("-", "_")
        .str.replace("/", "_")
        .str.replace("&", "and")
        .str.replace(r"[()]", "", regex=True)
    )

    # Standardize string fields
    for col in ["vcn_no.", "vessel_name", "anchorage___berth", "cargo_status", "shift_code"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.upper()

    # Parse all date/time columns
    date_cols = [
        "operation_d_and_t", "ata", "arrival_date", "first_line_ashore_d_and_t", 
        "vcd_vessel_completion_date", "sailing_time_movement_recording", "etd"
    ]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Clean numeric fields
    num_cols = [
        "shift_loaded_tonnage", "shift_discharged_tonnage", "shift_total_operation_hours", 
        "shift_total_event_hours", "delay_on_port_account_hrs", "delay_on_vessel_account_hrs"
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).clip(lower=0.0)

    # Resolve reference operational timestamps
    if "ata" in df.columns and "arrival_date" in df.columns:
        df["arr_ts"] = df["ata"].fillna(df["arrival_date"])
    elif "ata" in df.columns:
        df["arr_ts"] = df["ata"]
    else:
        df["arr_ts"] = df.get("arrival_date", pd.NaT)

    if "sailing_time_movement_recording" in df.columns and "etd" in df.columns:
        df["dept_ts"] = df["sailing_time_movement_recording"].fillna(df["etd"])
    elif "sailing_time_movement_recording" in df.columns:
        df["dept_ts"] = df["sailing_time_movement_recording"]
    else:
        df["dept_ts"] = df.get("etd", pd.NaT)

    df["berth_ts"] = df.get("first_line_ashore_d_and_t", pd.NaT)

    # Primary date identifier for filtering (use operation date, fallback to arrival)
    df["ref_date"] = df.get("operation_d_and_t", pd.NaT).fillna(df["arr_ts"])

    # --------------------------------------------------------------------------
    # Filter for Past 3 Months (Last 90 Days)
    # --------------------------------------------------------------------------
    valid_dates = df["ref_date"].dropna()
    if valid_dates.empty:
        print("Warning: No valid dates found to apply date filter. Processing all records.")
    else:
        # Determine latest date in dataset or current system date
        end_date = valid_dates.max()
        start_date = end_date - pd.Timedelta(days=90)
        
        print(f"Filtering Date Range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        df = df[(df["ref_date"] >= start_date) & (df["ref_date"] <= end_date)].copy()

    # Shift-level metrics
    loaded = df["shift_loaded_tonnage"] if "shift_loaded_tonnage" in df.columns else 0.0
    discharged = df["shift_discharged_tonnage"] if "shift_discharged_tonnage" in df.columns else 0.0
    df["shift_throughput_tons"] = loaded + discharged

    op_hrs = df.get("shift_total_operation_hours", 0.0)
    evt_hrs = df.get("shift_total_event_hours", 0.0)
    total_active = op_hrs + evt_hrs
    df["shift_efficiency_pct"] = np.where(total_active > 0, (op_hrs / total_active) * 100.0, 0.0)

    # Aggregate by Voyage (VCN)
    voyage_records = []
    for vcn, grp in df.groupby("vcn_no."):
        vessel = grp["vessel_name"].iloc[0]
        berth = grp["anchorage___berth"].iloc[0]
        cargo = grp["cargo_status"].iloc[0] if "cargo_status" in grp else "UNKNOWN"

        arr = grp["arr_ts"].dropna().min()
        berth_start = grp["berth_ts"].dropna().min()
        dept = grp["dept_ts"].dropna().max()
        comp = grp["vcd_vessel_completion_date"].dropna().max() if "vcd_vessel_completion_date" in grp else pd.NaT

        tot_loaded = grp["shift_loaded_tonnage"].sum() if "shift_loaded_tonnage" in grp else 0.0
        tot_discharged = grp["shift_discharged_tonnage"].sum() if "shift_discharged_tonnage" in grp else 0.0
        tot_throughput = tot_loaded + tot_discharged

        port_del = grp["delay_on_port_account_hrs"].sum() if "delay_on_port_account_hrs" in grp else 0.0
        vsl_del = grp["delay_on_vessel_account_hrs"].sum() if "delay_on_vessel_account_hrs" in grp else 0.0

        # Calculations in Hours
        pbwt = (berth_start - arr).total_seconds() / 3600.0 if pd.notnull(arr) and pd.notnull(berth_start) else np.nan
        end_point = dept if pd.notnull(dept) else comp
        stay = (end_point - berth_start).total_seconds() / 3600.0 if pd.notnull(berth_start) and pd.notnull(end_point) else np.nan
        vtt = (dept - arr).total_seconds() / 3600.0 if pd.notnull(arr) and pd.notnull(dept) else np.nan

        voyage_records.append({
            "VCN_No": vcn,
            "Vessel_Name": vessel,
            "Berth": berth,
            "Cargo_Status": cargo,
            "Arrival_Time": arr,
            "Berthing_Time": berth_start,
            "Departure_Time": dept,
            "Loaded_Tons": round(tot_loaded, 2),
            "Discharged_Tons": round(tot_discharged, 2),
            "Total_Throughput_Tons": round(tot_throughput, 2),
            "PBWT_Hours": round(max(0.0, pbwt), 2) if pd.notnull(pbwt) else np.nan,
            "Berth_Stay_Hours": round(max(0.0, stay), 2) if pd.notnull(stay) else np.nan,
            "VTT_Hours": round(max(0.0, vtt), 2) if pd.notnull(vtt) else np.nan,
            "Port_Delay_Hours": round(port_del, 2),
            "Vessel_Delay_Hours": round(vsl_del, 2)
        })

    voyages_df = pd.DataFrame(voyage_records)

    # Berth-level KPI Aggregation
    berths_clean = voyages_df[~voyages_df["Berth"].isin(["NAN", "NONE", "", np.nan])]
    berth_kpi = berths_clean.groupby("Berth").agg(
        Total_Calls=("VCN_No", "count"),
        Total_Throughput_Tons=("Total_Throughput_Tons", "sum"),
        Avg_PBWT_Hours=("PBWT_Hours", "mean"),
        Avg_Berth_Stay_Hours=("Berth_Stay_Hours", "mean"),
        Avg_VTT_Hours=("VTT_Hours", "mean"),
        Avg_Port_Delay_Hours=("Port_Delay_Hours", "mean"),
        Avg_Vessel_Delay_Hours=("Vessel_Delay_Hours", "mean")
    ).reset_index().round(2).sort_values(by="Total_Throughput_Tons", ascending=False)

    # Shift-level KPI Aggregation
    shifts_clean = df[~df["shift_code"].isin(["NAN", "NONE", "", np.nan])]
    shift_kpi = shifts_clean.groupby("shift_code").agg(
        Vessels_Handled=("vcn_no.", "nunique"),
        Total_Throughput_Tons=("shift_throughput_tons", "sum"),
        Avg_Operation_Hours=("shift_total_operation_hours", "mean"),
        Avg_Efficiency_Rate_Pct=("shift_efficiency_pct", "mean"),
        Total_Port_Delay_Hours=("delay_on_port_account_hrs", "sum"),
        Total_Vessel_Delay_Hours=("delay_on_vessel_account_hrs", "sum")
    ).reset_index().round(2).sort_values(by="shift_code")

    # Export to multi-tab Excel
    print(f"\nWriting 3-Month Report to: {OUTPUT_EXCEL}")
    with pd.ExcelWriter(OUTPUT_EXCEL, engine="openpyxl") as writer:
        berth_kpi.to_excel(writer, sheet_name="Berth_KPIs_3M", index=False)
        shift_kpi.to_excel(writer, sheet_name="Shift_KPIs_3M", index=False)
        voyages_df.to_excel(writer, sheet_name="Voyage_Details_3M", index=False)

    print("\n--- Past 3 Months Berth Performance Summary ---")
    print(berth_kpi.to_string(index=False))
    print(f"\nReport ready: {OUTPUT_EXCEL}")

if __name__ == "__main__":
    run_pipeline()