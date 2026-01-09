# How to Upload Files to GitHub

This guide explains several methods to upload files to GitHub repositories.

## Method 1: Using the GitHub Web Interface

The easiest way for beginners to upload files directly through the browser:

1. **Navigate to Your Repository**
   - Go to https://github.com/andywhitton/OWFS-MQTT-Sqllite-Temp-Monitoring
   - Sign in to your GitHub account

2. **Upload Files**
   - Click on "Add file" button (top right)
   - Select "Upload files"
   - Drag and drop files or click "choose your files"
   - Add a commit message describing what you're uploading
   - Click "Commit changes"

**Pros:** Simple, no tools needed, works from any computer  
**Cons:** Limited to small files, no folder structure preservation, slower for multiple files

---

## Method 2: Using Git Command Line

The most powerful and commonly used method by developers:

### Initial Setup (One-time)

1. **Install Git**
   ```bash
   # Ubuntu/Debian
   sudo apt-get install git
   
   # macOS (using Homebrew)
   brew install git
   
   # Windows: Download from https://git-scm.com/download/win
   ```

2. **Configure Git**
   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "your.email@example.com"
   ```

### Uploading Files

#### Option A: Clone and Add Files

1. **Clone the Repository**
   ```bash
   git clone https://github.com/andywhitton/OWFS-MQTT-Sqllite-Temp-Monitoring.git
   cd OWFS-MQTT-Sqllite-Temp-Monitoring
   ```

2. **Add Your Files**
   ```bash
   # Copy your files into the repository folder
   cp /path/to/your/file.py .
   ```

3. **Stage the Files**
   ```bash
   # Add specific files
   git add file.py
   
   # Or add all new/changed files
   git add .
   ```

4. **Commit the Changes**
   ```bash
   git commit -m "Add temperature monitoring script"
   ```

5. **Push to GitHub**
   ```bash
   git push origin main
   ```

#### Option B: Initialize Existing Folder

If you already have files in a folder:

1. **Navigate to Your Folder**
   ```bash
   cd /path/to/your/project
   ```

2. **Initialize Git**
   ```bash
   git init
   ```

3. **Add Remote Repository**
   ```bash
   git remote add origin https://github.com/andywhitton/OWFS-MQTT-Sqllite-Temp-Monitoring.git
   ```

4. **Add and Commit Files**
   ```bash
   git add .
   git commit -m "Initial commit"
   ```

5. **Push to GitHub**
   ```bash
   git branch -M main
   git push -u origin main
   ```

**Pros:** Full control, handles large files, works with entire folder structures  
**Cons:** Requires command line knowledge, initial setup required

---

## Method 3: Using GitHub Desktop

A graphical interface for Git operations:

1. **Install GitHub Desktop**
   - Download from https://desktop.github.com/
   - Install and sign in with your GitHub account

2. **Clone Repository**
   - File → Clone Repository
   - Search for "OWFS-MQTT-Sqllite-Temp-Monitoring"
   - Choose local path and clone

3. **Add Files**
   - Copy files into the repository folder on your computer
   - GitHub Desktop will automatically detect changes

4. **Commit and Push**
   - Review changed files in GitHub Desktop
   - Add commit message in bottom left
   - Click "Commit to main"
   - Click "Push origin" button

**Pros:** User-friendly GUI, visual diff viewing, easy for beginners  
**Cons:** Requires software installation, less flexible than command line

---

## Method 4: Using Git with VS Code

If you use Visual Studio Code:

1. **Open Folder in VS Code**
   ```bash
   code /path/to/OWFS-MQTT-Sqllite-Temp-Monitoring
   ```

2. **Initialize Repository** (if not already cloned)
   - Click Source Control icon (left sidebar)
   - Click "Initialize Repository"

3. **Add Files**
   - Create or copy files into the folder
   - Files appear in Source Control panel

4. **Commit Changes**
   - Stage files by clicking "+" next to each file
   - Enter commit message
   - Click checkmark to commit

5. **Push to GitHub**
   - Click "..." menu in Source Control
   - Select "Push"

---

## Best Practices

### What to Upload
- ✅ Source code files
- ✅ Configuration files (without secrets)
- ✅ Documentation (README, guides)
- ✅ Test files
- ✅ Build scripts

### What NOT to Upload
- ❌ Large binary files (use Git LFS instead)
- ❌ Passwords, API keys, tokens
- ❌ node_modules/ or other dependencies
- ❌ Build artifacts (.pyc, .class, .o files)
- ❌ IDE configuration (.vscode/, .idea/)
- ❌ Operating system files (.DS_Store, Thumbs.db)

### Use .gitignore

Create a `.gitignore` file to prevent unwanted files from being uploaded:

```
# Python
*.pyc
__pycache__/
*.so
*.egg-info/
dist/
build/

# Virtual environments
venv/
env/
ENV/

# Database files
*.db
*.sqlite
*.sqlite3

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Logs
*.log

# Environment variables
.env
secrets.json
```

---

## Common Issues and Solutions

### Authentication Issues

**Problem:** Push rejected due to authentication  
**Solution:** Use Personal Access Token (PAT)

1. Generate token: GitHub → Settings → Developer settings → Personal access tokens → Generate new token
2. Use token as password when pushing:
   ```bash
   git push origin main
   # Username: your-username
   # Password: paste-your-token-here
   ```
3. Or configure credential helper:
   ```bash
   git config --global credential.helper store
   ```

### File Too Large

**Problem:** File exceeds GitHub's 100MB limit  
**Solution:** Use Git Large File Storage (LFS)

```bash
git lfs install
git lfs track "*.db"
git add .gitattributes
git add large-file.db
git commit -m "Add large database file"
git push
```

### Merge Conflicts

**Problem:** Push rejected because remote has changes  
**Solution:** Pull first, resolve conflicts, then push

```bash
git pull origin main
# Resolve any conflicts in files
git add .
git commit -m "Merge remote changes"
git push origin main
```

---

## Quick Reference

```bash
# Clone repository
git clone https://github.com/andywhitton/OWFS-MQTT-Sqllite-Temp-Monitoring.git

# Check status
git status

# Add files
git add filename.py        # Add specific file
git add .                  # Add all changes

# Commit changes
git commit -m "Description of changes"

# Push to GitHub
git push origin main

# Pull latest changes
git pull origin main

# Create new branch
git checkout -b feature-branch

# Switch branches
git checkout main
```

---

## Additional Resources

- [GitHub Documentation](https://docs.github.com/)
- [Git Documentation](https://git-scm.com/doc)
- [GitHub Learning Lab](https://lab.github.com/)
- [Try Git Interactive Tutorial](https://try.github.io/)

---

## For This Repository

For the OWFS-MQTT-Sqllite-Temp-Monitoring project, typical files you might upload include:

- Python scripts for temperature monitoring
- MQTT configuration files
- Database schema files
- API endpoints
- Frontend HTML/CSS/JavaScript files
- Requirements.txt for Python dependencies
- Docker configuration (if used)
- Documentation and setup guides

Remember to use `.gitignore` to exclude database files with actual temperature data and any configuration files containing sensitive information like MQTT broker credentials.
