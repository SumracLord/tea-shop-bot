{ pkgs }: {
  # Replit: Python 3.13 и локали для корректной работы Streamlit/pandas.
  deps = [
    pkgs.python313
    pkgs.glibcLocales
  ];
}
