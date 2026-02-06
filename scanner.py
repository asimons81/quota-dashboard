import subprocess
import json
import sys
import os
from datetime import datetime

def fetch_usage():
    """
    Executes 'openclaw status --usage --json', parses the output,
    and saves a cleaned version to 'data.json'.
    """
    # Define paths relative to the script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_file = os.path.join(script_dir, 'data.json')

    try:
        # Execute the openclaw command with a 30-second timeout
        # Using shell=False for security
        result = subprocess.run(
            ['openclaw', 'status', '--usage', '--json'],
            capture_output=True,
            text=True,
            timeout=30,
            check=True
        )
        
        # Parse the JSON output from stdout
        raw_data = json.loads(result.stdout)
        
        # Navigate to the 'usage' section
        usage_info = raw_data.get('usage', {})
        providers = usage_info.get('providers', [])
        
        cleaned_usage = []
        # Models to filter OUT
        blacklist = ['gemini-2.5-pro', 'gemini-2.5-flash-thinking', 'gemini-2.5-flash-lite']
        
        for provider in providers:
            provider_name = provider.get('displayName', provider.get('provider', 'Unknown'))
            for window in provider.get('windows', []):
                label = window.get('label')
                # Skip blacklisted models
                if label in blacklist:
                    continue
                    
                # Extract specific fields: labels, percentages, and reset timestamps
                cleaned_usage.append({
                    'provider': provider_name,
                    'label': label,
                    'percentage': window.get('usedPercent'),
                    'resetAt': window.get('resetAt')  # Epoch timestamp in milliseconds
                })
        
        # Prepare the final structure with a 'lastUpdated' timestamp
        final_output = {
            'lastUpdated': datetime.utcnow().isoformat() + 'Z',
            'models': cleaned_usage
        }
        
        # Save to data.json with pretty printing
        with open(output_file, 'w') as f:
            json.dump(final_output, f, indent=2)
            
        print(f"Successfully updated {output_file} at {final_output['lastUpdated']}")
        
    except subprocess.TimeoutExpired:
        print("Error: The 'openclaw status' command timed out after 30 seconds.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error: 'openclaw status' failed with exit code {e.returncode}.", file=sys.stderr)
        if e.stderr:
            print(f"Stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON output from openclaw: {e}", file=sys.stderr)
        # Optionally log the raw output for debugging
        # print(f"Raw output: {result.stdout}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    fetch_usage()
